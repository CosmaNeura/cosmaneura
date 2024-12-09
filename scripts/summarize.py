# TODO
# - take a transcript filepath as input from stdin
# - take a example_note filepath as input from stdin
# - load the files from the filepaths
# - build a prompt from the transcript and example_note
# - prompt openai 4o with the prompt
# - build a second prompt to extract desired information from the 4o response as json
# - prompt openai 4o with the second prompt
# - destructure the second response to extract the desired information
# - add note to the information to indicate that the user consented to the call being recorded
# - save the information to a file in a ./notes directory

import os
import time
import json
from textwrap import dedent
from typing import IO
from dotenv import load_dotenv
from pdf2image import convert_from_path

from openai import OpenAI

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("API key not found. Please set OPENAI_API_KEY in your .env file.")
openai = OpenAI(api_key=api_key)

# def write_clinical_note(transcript: str, example_note: IO[bytes]) -> dict[str, str]:

# Convert images to base64 in a data URI format so they can be sent via API.
def get_img_uri(img):
    png_buffer = io.BytesIO()
    img.save(png_buffer, format="PNG")
    png_buffer.seek(0)
    base64_png = base64.b64encode(png_buffer.read()).decode('utf-8')
    return f"data:image/png;base64,{base64_png}"

def extract_note_schema(example_note: str) -> dict[str, str]:
    # https://cookbook.openai.com/examples/parse_pdf_docs_for_rag
    # Split the PDF into pages, then convert each page to an image.
    pdf_pages = convert_from_path(example_note, 500)
    pdf_page_uris = [get_img_uri(page) for page in pdf_pages]

    system_prompt = dedent(f"""
        You will be provided with a series of images of the pages of a PDF.
        The PDF is a clinical note created by a doctor from a mock patient encounter.
        This is NOT real medical information.
        What I want you to do is extract information from it.

        Here are your instructions:
        * Extract the different fields on this medical note.
        * Fields are in large font in the left column of the page and are separated by horizontal lines.
        * Don't extract any of the field values -- I don't care about the patient information.
        * Don't give any introduction or explanatory information. Just list the fields.
    """)

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"{data_uri}"
                        }
                    } for data_uri in pdf_page_uris
                ]
            },
        ],
        # TODO not sure if this is the right temperature or top_p.
        temperature=0,
        top_p=0.1
    )
    return response.choices[0].message.content

def build_unstructured_prompt(transcript: str, example_note: str) -> str | None:
    return dedent(f"""
        You are a medical professional tasked with creating a clinical note based on a transcript from a medical encounter. Your goal is to accurately summarize the key information from the encounter in a structured, professional format.

        Here is the transcript from the encounter:

        <transcript>
        {transcript}
        </transcript>

        Please create a clinical note based on the information provided in the transcript. The clinical note should be structured in the following format:

        <clinical_note>
        {note_schema}
        </clinical_note>

        As an example to help guide you, here is a good clinical note from another encounter.

        <example_note>
        {example_note}
        </example_note>

        When creating the clinical note:

        1. Carefully review the transcript and extract all relevant medical information.
        2. Organize the information into the appropriate sections of the clinical note format.
        3. Use clear, concise, and professional medical language.
        4. Avoid including any irrelevant personal details or small talk from the transcript.
        5. If certain information is not provided in the transcript, leave that section
        blank or write "Not mentioned in transcript" as appropriate. E.g. if the patient does not mention respiratory problems, do NOT assume that they do not have them.
        6. Ensure that all information in the clinical note is directly based on the content of the transcript. Do not add any speculative or assumed information.
        7. Imitate the style and conventions that you see in the example note.  But do NOT include any of the information from the example in the note you write. This is an example taken from a DIFFERENT patient.
        8. Take a deep breath and think step by step.

        Present your final clinical note within the <clinical_note> tags, following the structure outlined above. Ensure that each section is properly labeled with its respective XML tags.
    """)


def save_note(note, output_path):
    try:
        with open(output_path, "wb") as file:
            file.write(json.dumps(note))
        print(f"Clinical note saved to {output_path}")
    except Exception as e:
        print(f"Error saving note: {e}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Create a draft clinical note from a call transcript and example note"
    )
    parser.add_argument("transcript", type=str, help="Path to the transcript txt file")
    parser.add_argument("example_note", type=str, help="Path to the example note pdf file")
    args = parser.parse_args()

    # Read transcript and example note from disk.
    with open(args.transcript, "rb") as f:
        transcript = f.read()
    example_note = open(args.example_note, "rb")

    transcript_basename = os.path.basename(args.transcript)
    transcript_filename_no_ext = os.path.splitext(transcript_basename)[0]

    note = write_clinical_note(transcript, example_note)

    if note:
        timestamp = int(time.time())
        output_filename = f"./notes/{transcript_filename_no_ext}_{timestamp}.json"
        save_note(note, output_filename)
    else:
        print("Failed to generate note.")
