import json
from textwrap import dedent
from typing import Callable

from decouple import config

from app import log
from app.configuration.openai import openai

# The judge model used to grade live-LLM eval output. Defaults to the
# note-generation model (DEFAULT_MULTI_MODAL_MODEL in app/configuration/openai.py)
# because that's the deployment guaranteed to exist on every Azure OpenAI
# resource the app targets -- staging in particular has only this one model
# deployed today, so picking a different default 404s every eval at the judge
# step. Override via OPENAI_EVAL_MODEL when you have a cheaper judge model
# (e.g. gpt-4.1-mini-2025-04-14) available on your local resource.
OPENAI_MODEL = config("OPENAI_EVAL_MODEL", default="gpt-4.1-2025-04-14", cast=str)


def eval_n_of_m(call_llm: Callable[[], str], criteria: str, n: int, m: int) -> bool:
    """
    Call the provided function to generate an LLM output and evaluate it against the criteria using `eval`.
    This will be done up to `m` times, and the function returns True if at least `n` of the outputs satisfy the criteria.

    Args:
        get_llm_output: A callable that, when called, returns an LLM output (a string).
        criteria: The criteria to evaluate each LLM output against.
        n: The minimum number of successful evaluations needed.
        m: The maximum number of evaluations (calls to get_llm_output) to perform.

    Returns:
        True if at least `n` evaluations return True, otherwise False.
    """
    successes = 0
    for _ in range(m):
        output = call_llm()
        if eval_llm(output, criteria):
            successes += 1
            if successes >= n:
                return True
    raise AssertionError(
        f"Failed to satisfy criteria {criteria} {n}/{m} times, only succeeded {successes}/{m} times."
    )


def eval_llm(llm_output: str, criteria: str) -> bool:
    """
    Evaluate the LLM output against provided criteria.

    Args:
        llm_output: The output of the LLM.
        criteria: The criteria to evaluate against.

    Returns:
        True if the output meets the criteria, False otherwise.
    """
    return eval_llm_with_attmepts(llm_output, criteria, 1)


def eval_llm_with_attmepts(llm_output: str, criteria: str, attempts: int) -> bool:
    if attempts > 3:
        raise AssertionError(
            f"Model did not return proper schema after 3 attempts, criteria: {criteria}"
        )

    prompt = dedent(f"""
      I am going to give you the OUTPUT of a large language model. Your task is to evaluate the output against CRITERIA that I will provide. If the output satisfies the criteria, return True. Otherwise, return False.

      <OUTPUT>
      {llm_output}
      </OUTPUT>

      <CRITERIA>
      {criteria}
      </CRITERIA>

      Respond only in JSON with the following schema:

      {{
        satisfies_criteria: bool
      }}
    """)

    log.info("evaluating LLM output against criteria")

    response = openai.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )
    content = response.choices[0].message.content
    assert content

    answer = json.loads(content).get("satisfies_criteria")
    if answer is not None:
        return answer
    else:
        return eval_llm_with_attmepts(llm_output, criteria, attempts + 1)
