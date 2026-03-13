## Azure Deployment Plan — Radio Stream Recorder

This document describes an Azure architecture for running the radio recorder 24/7, capturing multiple stations in parallel, storing MP3 chunks, and notifying downstream systems when new chunks arrive.

### 1. High-level architecture

Conceptual diagram (text form):

```text
                +----------------------------+
                |   Azure DevOps / GitHub   |
                +-------------+--------------+
                              |
                              v
                     (A) CI builds image
                              |
                              v
                +----------------------------+
                |  Azure Container Registry  |
                +-------------+--------------+
                              |
                              v
                  (B) Deploy container app
                              |
                              v
    +-------------------------+-------------------------+
    |           Azure Container Apps (ACA)             |
    |   - One container per station (or per pod)       |
    |   - Pulls stream URL + station config            |
    +-------------------------+------------------------+
                              |
        Writes MP3 chunks     |
        to object storage     v
                    +---------------------------+
                    |   Azure Blob Storage      |
                    |   (Hot tier, container)   |
                    +-------------+-------------+
                                  |
             Storage events on new blobs (per prefix)
                                  v
                    +---------------------------+
                    |  Azure Event Grid         |
                    +-------------+-------------+
                                  |
                        Subscriptions to events
                                  v
           +----------------------+----------------------+
           |   Downstream systems / consumers           |
           |   - Azure Functions (e.g. metadata, STT)   |
           |   - Logic Apps / Service Bus queues        |
           +----------------------+----------------------+

Monitoring & logging:

```text
ACA logs / stdout ---> Azure Log Analytics Workspace
ACA metrics ---------> Azure Monitor / Alerts
Function logs -------> Application Insights
```

### 2. Azure service selection

#### 2.1 Running the recorder containers

- **Service**: **Azure Container Apps (ACA)** on the consumption or dedicated plan
- **Why ACA**:
  - Serverless container runtime with built-in autoscaling and revision management.
  - Simpler operational surface than AKS for this workload (no cluster management).
  - Easy to run one revision per recorder version and configure multiple replicas.
  - Integrated logging to Log Analytics.
- **Alternatives & trade-offs**:
  - **Azure Kubernetes Service (AKS)**:
    - Pros: Full control, advanced scheduling, complex multi-service workloads.
    - Cons: Overkill for “N long-running containers”, higher operational burden.
  - **Azure Container Instances (ACI)**:
    - Pros: Simple, quick to spin up.
    - Cons: Less suited for many always-on workloads; scaling and orchestration are more manual than ACA.

Recommended pattern:

- Deploy **one Container App per station**, each with:
  - `STREAM_URL`, `STATION_NAME`, `OUTPUT_DIR` (e.g. `/recordings`) as env vars.
  - Volume mount backed by Azure Blob Storage (via blobfuse2 or persisted storage in ACA if appropriate), or local volume with an external sync job.

#### 2.2 Storage for recorded audio chunks

- **Service**: **Azure Blob Storage**
  - One storage account with a container, for example: `radiorecordings`.
  - Path pattern: `station-name/YYYY/MM/DD/station-name_YYYYMMDD_HHMMSS.mp3`.
- **Why Blob Storage**:
  - Durable, cost-efficient object storage at scale.
  - Native eventing: Blob-created events can drive downstream workflows.
  - Support for lifecycle management (automatic tiering / deletion).
- **Alternatives & trade-offs**:
  - **Azure Files**: Better suited for shared POSIX-style filesystem semantics; not as ideal for large object archives with eventing.
  - **Azure Data Lake Storage Gen2**: Useful for big-data analytics pipelines; could be used if downstream is heavily analytics-oriented. For this use case, Blob Storage (which is effectively the same platform) is enough.

#### 2.3 Coordination / notifications when new chunks arrive

- **Primary choice**: **Azure Event Grid** on top of Blob Storage events.
  - Configure a `BlobCreated` event subscription filtered by container and optional path prefix (e.g. `subjectBeginsWith: /blobServices/default/containers/radiorecordings/blobs/station-name/`).
  - Targets:
    - **Azure Functions** for processing (e.g., metadata extraction, transcription).
    - **Azure Service Bus** for buffering events into queues or topics if ordering/backpressure is needed.
- **Why Event Grid**:
  - Native integration with Blob Storage; minimal boilerplate.
  - Push-based, low-latency notifications with per-event payloads (blob path, metadata).
  - Good fan-out for multiple downstream consumers.
- **Alternatives & trade-offs**:
  - **Polling from Functions / Jobs**: Simpler to reason about but less efficient, more lag, and more cost over time.
  - **Event Hubs**: Excellent for high-throughput streaming scenarios, but here events naturally originate from blob creation.

#### 2.4 Monitoring and logging

- **Services**:
  - **Azure Monitor** + **Log Analytics Workspace**
    - Collect stdout/stderr from Container Apps.
    - Container health metrics (CPU, memory, restarts).
  - **Application Insights** (for Functions or web apps that consume the audio).
- **Why this stack**:
  - Unified logging and metrics across container runtime and serverless functions.
  - Built-in alerting on metrics and log queries (e.g., “if a recorder has restarted more than X times in Y minutes”).

### 3. Justification of choices

- **Azure Container Apps** vs. AKS/ACI:
  - The workload is a set of similar, long-running containers. ACA provides:
    - Simple deployment from Container Registry.
    - Autoscaling rules (e.g., based on CPU or custom metrics).
    - No cluster management.
  - AKS would be appropriate only if you also had complex microservices or needed advanced networking/ingress routing at scale.
  - ACI is good for batch or bursty jobs, less ideal for always-on stream recorders.
- **Blob Storage**:
  - Fits time-based MP3 chunks very naturally.
  - Eventing via Event Grid simplifies downstream integration.
  - Lifecycle rules can automatically move older audio to Cool/Archive tiers or delete after retention period.
- **Event Grid + Functions**:
  - Push-based and low overhead: pay per event and function execution.
  - Each new chunk can trigger a function to handle indexing, metadata, transcodes, or notifications.
  - Easy to plug in additional consumers via new Event Grid subscriptions.

### 4. Scaling from 1 to 20 stations

#### Horizontal scaling pattern

- **Per-station Container App**:
  - Each station is a logical unit: configuration and environment variables specify its stream.
  - Scaling from 1 to 20 stations:
    - Deploy **20 Container Apps**, _or_
    - Use a parameterized app with multiple replicas, each configured for a different station via environment or DAPR bindings (slightly more complex).
  - Each instance:
    - Pulls from its station’s URL.
    - Writes to a dedicated prefix in Blob Storage (e.g., `radiorecordings/station-id/...`).

#### Resource-level scaling

- **Container compute**:
  - Tune CPU/memory for each Container App based on bitrate and expected load.
  - Use ACA autoscale policies primarily for resilience (e.g., restart on failure, maintain one replica per station).
- **Storage**:
  - Blob Storage scales automatically in capacity and throughput.
  - Design for:
    - Partition-friendly path layout (station and date in the path).
    - Potential lifecycle rules to control long-term size.
- **Downstream processing**:
  - Event Grid + Functions can scale out automatically with increasing event volume (more stations ⇒ more blobs ⇒ more events).

### 5. Cost awareness (rough estimate)

These are ballpark monthly numbers assuming:

- 20 stations, each streaming 24/7.
- Each station at 128 kbps MP3 output bitrate.
- Prices are approximate and would vary by region and date.

#### 5.1 Storage cost

Approximate data volume per station:

- 128 kbps = 16 KB/s.
- Per hour: \( 16 \text{ KB/s} \times 3600 \approx 57.6 \text{ MB} \).
- Per day: \( 57.6 \text{ MB} \times 24 \approx 1.38 \text{ GB} \).
- Per month (~30 days): \( 1.38 \text{ GB/day} \times 30 \approx 41.4 \text{ GB} \).

For 20 stations:

- \( 41.4 \text{ GB} \times 20 \approx 828 \text{ GB/month} \).

At a rough cost of **\$0.018/GB-month** (example hot tier price):

- Storage: \( 828 \times 0.018 \approx \$15/month \).

If you move older audio to Cool/Archive tiers or enforce retention, this can be significantly lower.

#### 5.2 Compute cost (Container Apps)

- Assume each recorder runs on a small resource allocation, for example:
  - 0.25 vCPU and 0.5 GB RAM per Container App.
- On a consumption plan, you pay for vCPU-seconds and GiB-seconds:
  - Rough approximation: say **\$15–\$30/month** for 20 very small always-on containers, depending on region and discounts.
- On a dedicated plan (e.g., 1–2 container app environment nodes), cost could be a fixed **\$50–\$150/month** depending on SKU.

These are directional numbers; actual billing must be checked against current Azure pricing and chosen SKUs.

#### 5.3 Eventing and serverless

- **Event Grid**:
  - Low per-million-operations cost; for 20 stations each creating a 10-minute chunk:
    - Each station: 6 chunks/hour × 24 × 30 ≈ 4,320 blobs/month.
    - 20 stations: 4,320 × 20 ≈ 86,400 events/month.
  - Well within the lowest pricing band; likely **\< \$1/month**.
- **Azure Functions**:
  - Pay-per-execution and duration-based.
  - For lightweight metadata/notification functions per blob, expect low single-digit dollars unless heavy processing is done.

### 6. Operational considerations

- **Secrets & configuration**:
  - Use **Azure Key Vault** for stream URLs and credentials (if any).
  - Reference secrets from Container Apps via managed identities.
- **Resilience**:
  - The Python app already restarts `ffmpeg` on failure.
  - At the platform level, configure ACA health probes and restart policies.
  - Alerting on:
    - High restart counts per app.
    - Gaps in blob creation for a given station (e.g., no new chunks for N minutes).
- **Security**:
  - Private network access for storage and container apps where possible (VNet integration).
  - Restrict access to storage account via private endpoints and appropriate IAM roles.

This setup gives you:

- A straightforward path from **live radio stream → MP3 chunks in Blob Storage**.
- Native **eventing** for downstream processing.
- A clear scaling story from 1 to 20 (or more) stations.
- Reasonable and predictable running costs with room for optimizations via lifecycle policies and right-sized compute.

