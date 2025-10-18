#!/bin/bash
# Manual FFmpeg test for Raspberry Pi

echo "=================================="
echo "🎥 Test Video Capture (5 seconds)"
echo "=================================="
echo "Testing /dev/video0 with YUYV format..."
timeout 5 ffmpeg -f v4l2 -input_format yuyv422 -video_size 640x480 -framerate 30 -i /dev/video0 -f null - 2>&1 | grep -E "(Input|Stream|error|Error|failed|Failed|Duration|frame=)" || echo "No output - command may have hung"
echo ""

echo "=================================="
echo "🎙️ Test Audio Capture (3 seconds)"
echo "=================================="
echo "Testing plughw:1,0 (HD camera microphone)..."
timeout 3 ffmpeg -f alsa -channels 1 -sample_rate 48000 -i plughw:1,0 -f null - 2>&1 | grep -E "(Input|Stream|error|Error|failed|Failed|Duration|size=)" || echo "No output - command may have hung"
echo ""

echo "Testing plughw:2,0 (USB Audio Device)..."
timeout 3 ffmpeg -f alsa -channels 1 -sample_rate 48000 -i plughw:2,0 -f null - 2>&1 | grep -E "(Input|Stream|error|Error|failed|Failed|Duration|size=)" || echo "No output - command may have hung"
echo ""

echo "=================================="
echo "🎬 Test Video + Audio Together"
echo "=================================="
echo "Testing /dev/video0 + plughw:1,0 for 5 seconds..."
timeout 5 ffmpeg \
  -f v4l2 -input_format yuyv422 -video_size 640x480 -framerate 30 -i /dev/video0 \
  -f alsa -channels 1 -sample_rate 48000 -i plughw:1,0 \
  -f null - 2>&1 | grep -E "(Input|Stream|error|Error|failed|Failed|Duration|frame=|time=)" || echo "No output - command may have hung"
echo ""

echo "=================================="
echo "🔍 Quick Device Status Check"
echo "=================================="
echo "Camera permissions:"
ls -l /dev/video0
echo ""
echo "Audio card status:"
cat /proc/asound/card1/pcm0c/sub0/status 2>/dev/null || echo "Cannot read audio status"
echo ""

echo "=================================="
echo "✅ Test Complete"
echo "=================================="
