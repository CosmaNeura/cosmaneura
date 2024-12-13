import json
import os
import time
import wave
from textwrap import dedent

import assemblyai as assembly
from decouple import config
from openai import OpenAI
from pydub import AudioSegment

from app import log

log.info("hello, david", structure="me")

deepgram_key = config("DEEPGRAM_API_KEY", cast=str)
if not deepgram_key:
    raise ValueError(
        "API key not found. Please set DEEPGRAM_API_KEY in your .env file."
    )

assembly_key = config("ASSEMBLY_API_KEY", cast=str)
if not assembly_key:
    raise ValueError(
        "API key not found. Please set ASSEMBLY_API_KEY in your .env file."
    )
assembly.settings.api_key = assembly_key

api_key = config("OPENAI_API_KEY", cast=str)
if not api_key:
    raise ValueError("API key not found. Please set OPENAI_API_KEY in your .env file.")
openai = OpenAI(api_key=api_key)

# current process:
# - job 1
#   - extract fields from example PDF note
#   - produce field descriptions
#   - create clinical note XML with descriptions
#   - extract example note from PDF
# - job 2
#   - generate transcript from audio file
#   - identify speakers as patient or doctor
#   - replace speaker designations with "Patient" and "Doctor"
# - job 3
#   - prompt 4o with transcript, example, and clinical note XML


def convert_to_wav(input_file, output_file="converted.wav"):
    audio = AudioSegment.from_file(input_file)
    audio.export(output_file, format="wav")
    return output_file


def extract_audio_from_mp4(mp4_path, output_audio_path="extracted_audio.wav"):
    audio = AudioSegment.from_file(mp4_path, format="mp4")
    audio.export(output_audio_path, format="wav")
    return output_audio_path


# Split into 2 MB chunks.
def split_audio(file_path, chunk_duration=10 * 1024 * 1024):
    with wave.open(file_path, "rb") as audio:
        params = audio.getparams()
        framerate = params.framerate
        n_channels = params.nchannels
        sampwidth = params.sampwidth
        chunk_frames = int(chunk_duration / (n_channels * sampwidth))
        chunks = []
        basename = os.path.splitext(file_path)[0]
        frame_num = 1
        while True:
            frames = audio.readframes(chunk_frames)
            if not frames:
                break
            chunk_filename = f"temp_chunk_{basename}_{frame_num}.wav"
            with wave.open(chunk_filename, "wb") as chunk:
                print(f"Writing temp file {chunk_filename}...")
                chunk.setnchannels(n_channels)
                chunk.setsampwidth(sampwidth)
                chunk.setframerate(framerate)
                chunk.writeframes(frames)
            chunks.append(chunk_filename)
            frame_num += 1
        return chunks


# Function to transcribe and concatenate results
def transcribe_audio(file_path):
    # ----------------------------------------------------------
    # ASSEMBLY AI Implementation
    # ----------------------------------------------------------
    transcriber = assembly.Transcriber()
    config = assembly.TranscriptionConfig(speaker_labels=True, speakers_expected=2)

    print("Transcribing audio...")
    transcript = transcriber.transcribe(file_path, config)
    print(transcript)

    if (
        transcript.status == assembly.TranscriptStatus.error
        or transcript.utterances is None
    ):
        print(f"Transcription failed: {transcript.error}")
        exit(1)

    # Identify speakers as doctor or patient.
    print("Identifying speakers...")
    sample = "\n".join([f"{u.speaker}: {u.text}" for u in transcript.utterances[:10]])
    classification = identify_doctor_from_transcript(sample)
    doctor_label = extract_doctor_classification(classification)

    full_transcription = []
    for utterance in transcript.utterances:
        label = utterance.speaker
        if doctor_label is not None:
            label = "Doctor" if doctor_label == utterance.speaker else "Patient"
        full_transcription.append(f"{label}: {utterance.text}")
    return "\n".join(full_transcription)
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # DEEPGRAM Implementation
    # ----------------------------------------------------------
    # try:
    #   # STEP 1 Create a Deepgram client using the API key
    #   deepgram = DeepgramClient(deepgram_key)
    #
    #   with open(file_path, "rb") as file:
    #       buffer_data = file.read()
    #
    #   payload: FileSource = {
    #       "buffer": buffer_data,
    #   }
    #
    #   options = PrerecordedOptions(
    #       model="nova-2",
    #       smart_format=True,
    #       paragraphs=True,
    #       punctuate=True,
    #       diarize=True, # identify speakers
    #       filler_words=False, # remove ums and ahs
    #   )
    #
    #   print(f"Transcribing audio...")
    #   response = deepgram.listen.rest.v("1").transcribe_file(payload, options)
    #
    #   # return response.to_json(indent=4)
    #   return response.results.channels[0].alternatives[0].paragraphs.transcript
    #
    # except Exception as e:
    #     print(f"Exception: {e}")

    # WHISPER implementation
    # -------------------------------------------------------
    # if not file_path.lower().endswith(".wav"):
    #     print("Converting audio file to WAV format...")
    #     file_path = convert_to_wav(file_path)
    #
    # chunks = split_audio(file_path)
    # full_transcription = []
    # try:
    #     for index, chunk in enumerate(chunks):
    #         print(f"Transcribing audio chunk #{index + 1}: {chunk}...")
    #         with open(chunk, "rb") as audio_file:
    #             response = client.audio.transcriptions.create(
    #                 model="whisper-1",
    #                 prompt="This is a recording of a doctor-patient interraction. There are only two speakers. MAKE SURE TO USE PUNCTUATION.",
    #                 temperature=0.8, # more deterministic
    #                 file=audio_file
    #             )
    #             full_transcription.append(response.text or "")
    #             full_transcription.append("\n")
    #         os.remove(chunk)
    # except Exception as e:
    #     print(f"Error processing chunk: {e}")
    #     os.remove(chunk)
    # return " ".join(full_transcription)
    # ----------------------------------------------------------


def identify_doctor_from_transcript(transcript: str) -> str | None:
    prompt = dedent(f"""
        I'm going to give you a transcript from a clinical encounter between a
        patient and a doctor. The transcript will have two speakers in it labeled A
        and B. One is the doctor and one is the patient. I want you to tell me which
        one you think the doctor is.

        <transcript>
        {transcript}
        </transcript>

        Take your time and think step by step. Which speaker label do you think
        corresponds with the doctor? What evidence do you have to support this
        theory? What are some quotes from the transcript that back up your decision?
    """)

    response = openai.chat.completions.create(
        model="gpt-4o-2024-08-06",
        messages=[
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": prompt},
        ],
    )

    reasoning = response.choices[0].message.content
    return reasoning if reasoning is not None else None


def extract_doctor_classification(reasoning: str | None) -> str | None:
    if reasoning is None:
        return None
    # TODO Use structured outputs instead of json mode. This allows for definition of
    # response schema with pydantic and better refusal handling.
    # https://platform.openai.com/docs/guides/structured-outputs
    prompt = dedent(f"""
        I'm going to give you the output of another LLM. The LLM was given a
        transcript of a clinical encounter between a patient and a doctor with
        the speakers labeled as A and B. The LLM was asked to identify
        which speaker (A or B) was the doctor. Here is its reasoning:

        <reasoning>
        {reasoning}
        </reasoning>

        Your job is to extract the LLM's classification from the reasoning above.
        Respond only in JSON format with the following schema:

        {{
          doctor: "A" | "B"
        }}
    """)

    response = openai.chat.completions.create(
        model="gpt-4o-2024-08-06",
        messages=[
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    # Parse and return the value of "doctor"
    response_content = response.choices[0].message.content
    if response_content is None:
        return None
    return json.loads(response_content)["doctor"]


def save_transcription(transcript, output_path):
    try:
        with open(output_path, "wb") as file:
            file.write(transcript)
        print(f"Transcription saved to {output_path}")
    except Exception as e:
        print(f"Error saving transcription: {e}")


if __name__ == "__main__":
    import argparse

    # Command-line argument parsing
    parser = argparse.ArgumentParser(
        description="Transcribe audio using OpenAI's Whisper API."
    )
    parser.add_argument(
        "audio_file", type=str, help="Path to the audio file to transcribe."
    )
    args = parser.parse_args()
    audio_file = args.audio_file

    file_basename = os.path.basename(audio_file)
    basename_no_ext = os.path.splitext(file_basename)[0]

    # Handle MP4 input
    if audio_file.lower().endswith(".mp4"):
        print("Extracting audio from MP4...")
        audio_file = extract_audio_from_mp4(
            audio_file, output_audio_path=f"temp_audio_{basename_no_ext}"
        )

    transcript = transcribe_audio(audio_file)
    if transcript:
        # Create the transcripts directory if it doesn't exist
        output_dir = "./transcripts"
        os.makedirs(output_dir, exist_ok=True)

        timestamp = int(time.time())
        output_filename = f"{basename_no_ext}_assembly_{timestamp}_transcript.txt"
        output_path = os.path.join(output_dir, output_filename)

        # Save transcription to file
        save_transcription(transcript, output_path)
    else:
        print("No transcription available.")
