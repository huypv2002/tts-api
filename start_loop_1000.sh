#!/bin/bash
cd "$(dirname "$0")"
mkdir -p tts_loop_1000
if [ -f tts_loop_1000/run.log ]; then
  mv tts_loop_1000/run.log "tts_loop_1000/run_$(date +%Y%m%d_%H%M%S).log"
fi
# kill previous pid if any
if [ -f tts_loop_1000/pid.txt ]; then
  old=$(cat tts_loop_1000/pid.txt)
  kill "$old" 2>/dev/null || true
  sleep 1
  kill -9 "$old" 2>/dev/null || true
fi
# HSW farm + token pool (1 token = 1 TTS, không TTL) + TTS workers
# tune: --workers TTS, --hsw-workers pages, --token-target ≈ workers
nohup python3 -u fast_tts_loop.py \
  --count 1000 \
  --outdir tts_loop_1000 \
  --text-file long_text.txt \
  --lang en \
  --workers 6 \
  --hsw-workers 3 \
  --token-target 6 \
  --max-per-ip 0 \
  > tts_loop_1000/run.log 2>&1 &
echo $! > tts_loop_1000/pid.txt
echo "started PID=$(cat tts_loop_1000/pid.txt) mp3=$(find tts_loop_1000 -name '*.mp3' | wc -l | tr -d ' ')"
