"""Before a reply goes out, a second, cheaper model looks for claims that would mislead the employee.

Code checks what it can match exactly (cited articles were read, ticket numbers exist). It can't match a sentence
saying what a ticket, a team or the helpdesk will do, or one restating what the employee said; this checks those
against the same evidence the agent had. It only flags: the agent rewrites once (agent.py).
"""

from pydantic import BaseModel, Field

CHECKER_PROMPT = """\
You check one reply an IT support agent is about to send to an employee. Flag only these, and only when nothing in \
the tool results, the session or the agent's rules supports them:
1. What the helpdesk, a ticket, an approval, another team or another person will do, allow or require, or how the \
employee can reach them.
2. The employee's own words, or a record, restated wrongly or with details they didn't give.
3. An action said to be done that the tool results don't show was done.
4. A cause or an outcome stated as certain when the evidence only makes it likely.
Don't flag general tips that are true almost anywhere, advice the reply labels as the agent's own or as general, \
questions, or anything the evidence or the rules state. The agent offering or describing what it can do below is \
supported.

What the agent can do (its tools, and the actions the policy engine decides on each time):
{capabilities}

The agent's rules (true statements about how this helpdesk works):
{rules}

Session:
{session}

The employee's messages so far:
{employee}

Tool results in this conversation:
{evidence}

Reply to check:
{reply}
"""


class Flags(BaseModel):
    harmful: list[str] = Field(description="Each flagged sentence, quoted, with a few words on what is unsupported. "
                                           "Empty if none.")


TIMEOUT_S = 15  # a slow check must not hold a reply: on timeout the caller lets the draft through


def find_harmful_claims(client, model: str, *, reply: str, rules: str, session: str, employee_messages: list[str],
                        tool_results: list[str], capabilities: list[str]):
    """The flagged sentences, and the response (for usage accounting)."""
    prompt = CHECKER_PROMPT.format(rules=rules, session=session, reply=reply, capabilities="\n".join(capabilities),
                                   employee="\n".join(f"- {m}" for m in employee_messages),
                                   evidence="\n".join(tool_results) or "none")
    response = client.with_options(timeout=TIMEOUT_S, max_retries=1).messages.parse(
        model=model, max_tokens=4000,  # the model thinks first; leave room for the answer
        messages=[{"role": "user", "content": prompt}], output_format=Flags)
    return response.parsed_output.harmful, response
