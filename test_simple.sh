#!/bin/bash
# Simple direct test without timeout

echo "Test 1: List video formats (should complete quickly)"
echo "======================================================"
ffmpeg -f v4l2 -list_formats all -i /dev/video0 2>&1 | head -40
echo ""

echo "Test 2: Capture 1 frame to file"
echo "======================================================"
ffmpeg -f v4l2 -input_format yuyv422 -video_size 640x480 -i /dev/video0 -frames:v 1 -y /tmp/test_frame.jpg 2>&1 | tail -10
if [ -f /tmp/test_frame.jpg ]; then
    ls -lh /tmp/test_frame.jpg
    echo "✅ Frame captured successfully!"
else
    echo "❌ Failed to capture frame"
fi
echo ""

echo "Test 3: List audio device info"
echo "======================================================"
arecord -L | grep -A2 "plughw:1,0"
echo ""

echo "Test 4: Test audio with arecord (ALSA direct)"
echo "======================================================"
timeout 2 arecord -D plughw:1,0 -f S16_LE -r 48000 -c 1 -d 1 /tmp/test_audio.wav 2>&1
if [ -f /tmp/test_audio.wav ]; then
    ls -lh /tmp/test_audio.wav
    echo "✅ Audio recorded successfully!"
else
    echo "❌ Failed to record audio"
fi
echo ""

echo "Test 5: Check user groups"
echo "======================================================"
groups
echo ""
echo "Video group members:"
getent group video
echo ""
echo "Audio group members:"
getent group audio
