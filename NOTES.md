## NOTES


TODO:
1. Python app that records radio streams and saves chunked 10-minute MP3 files. Must actually work
2. Containerize the app.
3. Correct README of setup instructions
4. Azure deployment plan
5. notes (this file)

This file is an informal log of implementation thoughts and trade-offs.



Used ffprobe "https://stream3.ehrhiti.lv:8000/Stream_93_LV01.aac" and got specs of the stream:
Codec AAC
Sample rate: 44100 Hz
Channels: stereo
Bitrate 101kb/s

EHR stream needs to be re-encoded to MP3 from AAC. (Source: 101kbps AAC stereo @ 44.1 kHz, Output: 128kbps MP3 stereo @ 44.1 kHz.)
dimensonesuonoroma stream needs to be re-encoded to MP3 from HLS AAC. (Source: 64 kbps AAC stereo @ 48 kHz, Output: 128 kbps MP3 stereo @ 48 kHz.)

Bitrate configurable in main.py class RecorderConfig. Not that much focus on compression necessary.

All timestamps in logs and filenames are in UTC to for simpler operations.

Tested both HLS and AAC streams. Both successfully write to mp3 files with correct formats, locally and docker.

What happens if I turn off internet connection while app is running and actively writing a segment??
Looks like it recovers nicely, continuing from the point of last connection in the same segment.

Made a logger which logs all actions performed by the code, including connections to streams and saved files and their names.

