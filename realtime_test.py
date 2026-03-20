"""
실시간 STT (Speech-to-Text) 프로토타입
- 마이크 입력을 실시간으로 감지
- 말이 끝난 구간을 자동 감지 (VAD)
- OpenAI Whisper API로 텍스트 변환
 
필요 패키지 설치:
    pip install openai sounddevice numpy scipy
 
실행 방법:
    OPENAI_API_KEY=your_key_here python realtime_stt.py
    또는 스크립트 내 API_KEY 변수에 직접 입력
"""
 
import os
import io
import time
import threading
import queue
import tempfile
import wave
 
import numpy as np
import sounddevice as sd
from scipy.io import wavfile
from openai import OpenAI
 
# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
API_KEY         = os.getenv("OPENAI_API_KEY", "")
LANGUAGE        = "ko"          # 언어 코드 (ko=한국어, en=영어, ja=일본어 등)
SAMPLE_RATE     = 16000         # 샘플레이트 (Whisper 권장: 16000)
CHANNELS        = 1             # 모노
CHUNK_DURATION  = 0.1           # 오디오 버퍼 단위 (초)
SILENCE_DURATION = 1.2          # 이 시간(초) 이상 조용하면 → 문장 완성으로 판단
SILENCE_THRESHOLD = 0.02        # 볼륨 임계값 (이 이하 = 무음)
MIN_SPEECH_DURATION = 0.8       # 최소 발화 길이 (초) - 너무 짧은 노이즈 제거
MAX_CHUNK_DURATION  = 8.0       # 최대 버퍼 길이 (초) - 말이 계속 이어져도 강제 전송
 

 # Whisper 환각 필터 - 자주 나오는 가짜 문구 목록
HALLUCINATION_PATTERNS = [
    "mbc", "kbs", "sbs", "뉴스", "이덕영", "앵커",
    "시청해주셔서 감사합니다", "구독과 좋아요", "구독", "좋아요", "알림설정",
    "감사합니다", "안녕하세요", "bye", "thank you for watching",
    "자막 제공", "번역 제공", "subtitles", "copyright",
]
 
# ─────────────────────────────────────────
# 핵심 클래스
# ─────────────────────────────────────────
class RealtimeSTT:
    def __init__(self):
        self.client = OpenAI(api_key=API_KEY)
        self.audio_queue = queue.Queue()        # 오디오 청크 큐
        self.is_running = False
 
        # 상태 추적
        self.speech_buffer = []                 # 현재 발화 버퍼
        self.is_speaking = False
        self.silence_start = None
        self.speech_start = None                # 발화 시작 시각 (최대 길이 체크용)
 
    def _rms(self, audio_chunk: np.ndarray) -> float:
        """오디오 청크의 볼륨(RMS) 계산"""
        return float(np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2))) / 32768
 
    def _transcribe(self, audio_data: np.ndarray) -> str:
        """Whisper API로 음성 → 텍스트 변환"""
        # numpy 배열 → WAV 바이트 변환
        with io.BytesIO() as wav_buffer:
            with wave.open(wav_buffer, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)              # 16-bit
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_data.tobytes())
            wav_buffer.seek(0)
            wav_bytes = wav_buffer.read()
 
        # 임시 파일로 저장 (OpenAI API는 파일 객체 필요)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
 
        try:
            with open(tmp_path, "rb") as audio_file:
                result = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=LANGUAGE,
                )
            return result.text.strip()
        finally:
            os.unlink(tmp_path)
 
    def _flush_buffer(self, force=False):
        """버퍼에 쌓인 음성을 전사하고 초기화"""
        full_audio = np.concatenate(self.speech_buffer, axis=0).flatten()
        duration = len(full_audio) / SAMPLE_RATE
 
        if duration >= MIN_SPEECH_DURATION:
            print("(인식 중...)", end="\r", flush=True)
            try:
                text = self._transcribe(full_audio)
                if text:
                    print(f"\r💬 {text}          ")
            except Exception as e:
                print(f"\r❌ 오류: {e}")
        else:
            print("\r", end="", flush=True)    # 너무 짧은 발화 무시
 
        # 강제 분할(force=True)이면 발화 상태는 유지, 버퍼만 초기화
        self.speech_buffer = []
        self.silence_start = None
        self.speech_start = time.time() if force else None
        if not force:
            self.is_speaking = False
 
    def _audio_callback(self, indata, frames, time_info, status):
        """마이크 입력 콜백 (별도 스레드에서 실행)"""
        if status:
            pass  # 오디오 에러 무시
        self.audio_queue.put(indata.copy())
 
    def _process_loop(self):
        """오디오 큐를 소비하며 VAD + 전사 처리"""
        chunks_per_second = int(1.0 / CHUNK_DURATION)
 
        while self.is_running:
            try:
                chunk = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
 
            volume = self._rms(chunk)
            is_speech = volume > SILENCE_THRESHOLD
 
            if is_speech:
                # 말하는 중
                if not self.is_speaking:
                    self.is_speaking = True
                    self.speech_start = time.time()
                    print("\n🎤 ", end="", flush=True)
                self.silence_start = None
                self.speech_buffer.append(chunk)
 
            else:
                # 무음 구간
                if self.is_speaking:
                    self.speech_buffer.append(chunk)     # 무음도 버퍼에 포함
 
                    if self.silence_start is None:
                        self.silence_start = time.time()
 
                    elapsed_silence = time.time() - self.silence_start
 
                    if elapsed_silence >= SILENCE_DURATION:
                        # 무음 감지 → 발화 완료
                        self._flush_buffer()
 
            # 말이 계속 이어져도 MAX_CHUNK_DURATION 초과 시 강제 전송
            if self.is_speaking and self.speech_start is not None:
                if time.time() - self.speech_start >= MAX_CHUNK_DURATION:
                    print(" ✂️ (분할 전송)", end="", flush=True)
                    self._flush_buffer(force=True)
 
    def start(self):
        """STT 시작"""
        print("=" * 50)
        print("  실시간 STT 시작")
        print(f"  언어: {LANGUAGE} | 무음 감지: {SILENCE_DURATION}초 | 최대 청크: {MAX_CHUNK_DURATION}초")
        print("  말하면 자동으로 텍스트로 변환됩니다.")
        print("  종료: Ctrl+C")
        print("=" * 50)
 
        self.is_running = True
 
        # 처리 스레드 시작
        process_thread = threading.Thread(target=self._process_loop, daemon=True)
        process_thread.start()
 
        # 마이크 스트림 시작
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype='int16',
            blocksize=int(SAMPLE_RATE * CHUNK_DURATION),
            callback=self._audio_callback,
        ):
            try:
                while True:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                print("\n\n⏹ 종료합니다.")
                self.is_running = False
 
 
# ─────────────────────────────────────────
# 실행
# ─────────────────────────────────────────
if __name__ == "__main__":
    if API_KEY == "여기에_API_KEY_입력":
        print("⚠️  API 키를 설정해주세요!")
        print("   방법 1: 환경변수  →  export OPENAI_API_KEY=sk-...")
        print("   방법 2: 스크립트 상단 API_KEY 변수에 직접 입력")
        exit(1)
 
    stt = RealtimeSTT()
    stt.start()
 