# Extract the schema and content from an example clinical note.

# Unfortunately this script requires poppler to be installed on your system.
#   `brew install poppler` on MacOS
#   `apt-get install poppler-utils` on Ubuntu

import base64
import io
import os
import json
from textwrap import dedent
from dotenv import load_dotenv
from pdf2image import convert_from_path
import PIL.Image

from openai import OpenAI

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("API key not found. Please set OPENAI_API_KEY in your .env file.")
openai = OpenAI(api_key=api_key)

# Convert images to base64 in a data URI format so they can be sent via API.
def get_img_uri(img: PIL.Image.Image) -> str:
    png_buffer = io.BytesIO()
    img.save(png_buffer, format="PNG")
    png_buffer.seek(0)
    base64_png = base64.b64encode(png_buffer.read()).decode('utf-8')
    return f"data:image/png;base64,{base64_png}"

# Extract the fields from an example clinical note.
def extract_note_fields(example_note: str) -> list[str] | None:
    # https://cookbook.openai.com/examples/parse_pdf_docs_for_rag
    # Split the PDF into pages, then convert each page to an image.
    pdf_pages = convert_from_path(example_note, 500)
    pdf_page_uris = [get_img_uri(page) for page in pdf_pages]

    system_prompt = dedent("""
        You will be provided with a series of images of the pages of a PDF.
        The PDF is a clinical note created by a doctor from a patient encounter.
        What I want you to do is extract information from it.

        Here are your instructions:
        * Extract the different fields on this medical note.
        * Fields are in large font in the left column of the page and are separated by horizontal lines.
        * Don't extract any of the field values -- I don't care about the patient information.
        * Don't give any introduction or explanatory information. Just list the fields.
        * Respond only in JSON format with the following schema:

        {{
          fields: string[]
        }}
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
        response_format={"type": "json_object"},
        # TODO I'm not sure if this is a good temperature or top_p.
        temperature=0,
        top_p=0.1
    )

    fields = response.choices[0].message.content
    if fields is None: return None
    return json.loads(fields)["fields"]

def explain_note_fields(fields: list[str]) -> dict[str, str] | None:
    prompt = dedent(f"""
        I have extracted the following fields from a clinical note:

        {fields}

        Please provide a brief instruction for each field. The instruction
        should give general guidance to another doctor on what to include in
        this portion of the note. For example:

        - field="CC", instruction="Summarize the main reason for the patient's visit in one sentence"
        - field="History of Present Illness", instruction="Provide a detailed description of the patient's current symptoms, including onset, duration, severity, and any relevant contextual information"
        - field="Assessment", instruction="List the primary diagnosis and any secondary diagnoses, along with the rationale for each diagnosis"

        Respond in JSON format such that object keys are the field names and values are their instructions.

        {{
          fieldName: instructions
        }}
    """)

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
        ],
        response_format={"type": "json_object"},
        # TODO I'm not sure if this is a good temperature or top_p.
        temperature=0,
        top_p=0.1
    )

    instructions = response.choices[0].message.content
    if instructions is None: return None
    return json.loads(instructions)

def extract_note_content(example_note: str, fields: list[str]) -> dict[str, str] | None:
    # https://cookbook.openai.com/examples/parse_pdf_docs_for_rag
    # Split the PDF into pages, then convert each page to an image.
    pdf_pages = convert_from_path(example_note, 500)
    pdf_page_uris = [get_img_uri(page) for page in pdf_pages]

    system_prompt = dedent("""
        You will be provided with a series of images of the pages of a PDF.
        The PDF is a clinical note created by a doctor from a patient encounter.
        What I want you to do is extract information from it.
        You will also be provided with a list of fields in the clinical note. Extract the content for each field.

        Fields to extract:
        {fields}

        Here are your instructions:
        * Extract the content in the note for each of the fields provided.
        * Fields are in large font in the left column of the page and are separated by horizontal lines.
        * Field content is in smaller font in the right column of the page.
        * Content can span multiple pages of the PDF.
        * Don't give any introduction or explanatory information. Just list the field content.
        * Respond only in JSON format such that object keys are the field names and values are their associated content.

        {{
          fieldName: content
        }}
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
        response_format={"type": "json_object"},
        # TODO I'm not sure if this is a good temperature or top_p.
        temperature=0,
        top_p=0.1
    )

    content = response.choices[0].message.content
    if content is None: return None
    return json.loads(content)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract the schema and content from an example clinical note."
    )
    parser.add_argument("example_note", type=str, help="Path to the example note pdf file")
    args = parser.parse_args()

    fields = extract_note_fields(args.example_note)
    print(fields)

    instructions = explain_note_fields(fields)
    print(json.dumps(instructions, indent=2))

    example_basename = os.path.basename(args.example_note)
    example_filename_no_ext = os.path.splitext(example_basename)[0]

    output_dir = "./schemas"
    os.makedirs(output_dir, exist_ok=True)

    with open(f"{output_dir}/{example_filename_no_ext}.json", "w") as file:
        json.dump(instructions, file, indent=2)

    content = extract_note_content(args.example_note, fields)
    print(json.dumps(content, indent=2))

    output_dir = "./examples"
    os.makedirs(output_dir, exist_ok=True)
    with open(f"{output_dir}/{example_filename_no_ext}.json", "w") as file:
        json.dump(content, file, indent=2)
