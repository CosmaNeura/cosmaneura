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

import json
import os
import time
from textwrap import dedent

from decouple import config
from openai import OpenAI

api_key = config("OPENAI_API_KEY", cast=str)
if not api_key:
    raise ValueError("API key not found. Please set OPENAI_API_KEY in your .env file.")
openai = OpenAI(api_key=api_key)


# Build a structured prompt for creating a clinical note based on a transcript.
# This is structured in the sense that it expects a JSON response with specific
# fields. This may not be optimal for our usecase, but it's a good starting
# point. See https://arxiv.org/html/2408.02442v3 for more information.
def build_structured_prompt(
    transcript: str, example_note: dict[str, str], note_schema: dict[str, str]
) -> str:
    return dedent(f"""
        You are a medical professional tasked with creating a clinical note based on a transcript from a medical encounter. Your goal is to accurately summarize the key information from the encounter in a structured, professional format.

        Here is the transcript from the encounter:

        <transcript>
        {transcript}
        </transcript>

        Please create a clinical note based on the information provided in the transcript.
        The clinical note should have the following schema and be structured in JSON format:

        <schema>
        {json.dumps(note_schema, indent=2)}
        </schema>

        As an example to help guide you, here is a good clinical note from another encounter.

        <example_note>
        {json.dumps(example_note, indent=2)}
        </example_note>

        When creating the clinical note:

        1. Carefully review the transcript and extract all relevant medical information.
        2. Organize the information into the appropriate sections of the clinical note schema.
        3. Use clear, concise, and professional medical language.
        4. Avoid including any irrelevant personal details or small talk from the transcript.
        5. If certain information is not provided in the transcript, leave that section blank or write "Not mentioned in transcript" as appropriate. E.g. if the patient does not mention respiratory problems, do NOT assume that they do not have them.
        6. Ensure that all information in the clinical note is directly based on the content of the transcript. Do not add any speculative or assumed information.
        7. Imitate the style and conventions that you see in the example note.  But do NOT include any of the information from the example in the note you write. This is an example taken from a DIFFERENT patient.
        8. Take a deep breath and think step by step.

        Respond only in JSON format with the schema above.
    """)


def write_clinical_note(
    transcript: str, example_note: dict[str, str], note_schema: dict[str, str]
) -> dict[str, str] | None:
    prompt = build_structured_prompt(transcript, example_note, note_schema)
    print("Prompting 4o with:")
    print(prompt)
    # Note: we can't currently use structured-outputs with the chat endpoint
    # because our schema is determined at runtime. Doctors all seem to have a
    # unique note structure that they like. Tebra gives them pretty broad
    # abilities to add fields and sections to their notes and they do.
    # https://platform.openai.com/docs/guides/structured-outputs
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )

    content = response.choices[0].message.content
    if content is None:
        return None

    # TODO if content schema doesn't match note_schema, we need to retry.

    print("Response:")
    print(content)

    return json.loads(content)


def save_note(note, output_path):
    try:
        with open(output_path, "w") as f:
            f.write(json.dumps(note, indent=2))
        print(f"Clinical note saved to {output_path}")

        # print a non-json version of the note for easy reading to stdout
        print("Note:")
        for field, value in note.items():
            print(f"{field}:")
            print(f"{value}\n")

    except Exception as e:
        print(f"Error saving note: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Create a draft clinical note from a call transcript and example note"
    )
    parser.add_argument(
        "schema", type=str, help="Path to desired note schema json file"
    )
    parser.add_argument("example", type=str, help="Path to the example note json file")
    parser.add_argument("transcript", type=str, help="Path to the transcript txt file")
    args = parser.parse_args()

    with open(args.transcript, "r", encoding="utf-8") as f:
        transcript = f.read()
    with open(args.example, "rb") as f:
        example_note = json.loads(f.read())
    with open(args.schema, "rb") as f:
        note_schema = json.loads(f.read())

    # TODO Validate the example note matches the schema.

    transcript_basename = os.path.basename(args.transcript)
    transcript_filename_no_ext = os.path.splitext(transcript_basename)[0]

    # TODO need to add post-summarization text to the relevant sections, see
    # notes here:
    # https://docs.google.com/document/d/1ZOHngE8cJjRu9AP4jOw1NFfsjy9wM_EUGJNjRrD_33U/edit?tab=t.0
    note = write_clinical_note(transcript, example_note, note_schema)

    output_dir = "./notes"
    os.makedirs(output_dir, exist_ok=True)

    if note:
        timestamp = int(time.time())
        output_filename = f"{output_dir}/{transcript_filename_no_ext}_{timestamp}.json"
        save_note(note, output_filename)
    else:
        print("Failed to generate note.")
