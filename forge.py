import os
import json
import requests
import subprocess
import re
from pathlib import Path
from dataclasses import dataclass

success_definition = None
evidence = []

# ---------- WORKSPACE ----------

WORKSPACE = Path(__file__).parent.parent / "workspace"


# ---------- TOOL CALL ----------

@dataclass
class ToolCall:
    name: str
    arguments: dict


# ---------- TOOLS ----------

def list_files():
    files = []

    for path in WORKSPACE.rglob("*"):
        if path.is_file():
            files.append(str(path.relative_to(WORKSPACE)))

    return files


def read_file(filename):
    path = (WORKSPACE / filename).resolve()

    if not path.is_relative_to(WORKSPACE.resolve()):
        raise ValueError("Access outside workspace is not allowed")

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {filename}")

    return path.read_text(encoding="utf-8")

def write_file(filename, content):
    path = (WORKSPACE / filename).resolve()

    if not path.is_relative_to(WORKSPACE.resolve()):
        raise ValueError("Access outside workspace is not allowed")

    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(content, encoding="utf-8")

    return f"Successfully wrote {filename}"

def run_command(command):
    result = subprocess.run(
        command,
        cwd=WORKSPACE,
        shell=True,
        capture_output=True,
        text=True,
        timeout=30
    )

    return {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }

def search_files(query):
    matches = []

    for root, dirs, files in os.walk(WORKSPACE):
        # Don't search Python cache directories
        dirs[:] = [d for d in dirs if d != "__pycache__"]

        for filename in files:
            filepath = os.path.join(root, filename)

            try:
                with open(filepath, "r", encoding="utf-8") as file:
                    for line_number, line in enumerate(file, start=1):
                        if query.lower() in line.lower():
                            relative_path = os.path.relpath(
                                filepath,
                                WORKSPACE
                            )

                            matches.append(
                                f"{relative_path}:{line_number}:{line.rstrip()}"
                            )

            except (UnicodeDecodeError, PermissionError):
                continue

    return matches

# ---------- TOOL REGISTRY ----------

TOOLS = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "run_command": run_command,
    "search_files": search_files,
}


def execute_tool(name, arguments):
    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name}")

    return TOOLS[name](**arguments)


# ---------- TOOL PARSER ----------

def parse_tool_call(answer):
    text = answer.strip()

    # First, try the entire response as JSON.
    try:
        data = json.loads(text)

        if (
            isinstance(data, dict)
            and isinstance(data.get("tool"), str)
            and isinstance(data.get("arguments"), dict)
        ):
            return ToolCall(
                name=data["tool"],
                arguments=data["arguments"]
            )

    except json.JSONDecodeError:
        pass

    # If the response contains Markdown, explanations,
    # or multiple JSON objects, scan for individual objects.
    for start in range(len(text)):
        if text[start] != "{":
            continue

        depth = 0
        in_string = False
        escape = False

        for end in range(start, len(text)):
            char = text[end]

            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True

            elif char == "{":
                depth += 1

            elif char == "}":
                depth -= 1

                if depth == 0:
                    candidate = text[start:end + 1]

                    try:
                        data = json.loads(candidate)
                    except json.JSONDecodeError:
                        break

                    if (
                        isinstance(data, dict)
                        and isinstance(data.get("tool"), str)
                        and isinstance(data.get("arguments"), dict)
                    ):
                        return ToolCall(
                            name=data["tool"],
                            arguments=data["arguments"]
                        )

                    break

    return None
# ---------- TOOL VALIDATION ----------

def validate_tool_call(tool_call):
    if tool_call.name not in TOOLS:
        raise ValueError(
            f"Unknown tool: {tool_call.name}"
        )

    return True


# ---------- MODEL ----------

messages = [
    {
        "role": "system",
        "content": """You are Forge, a coding agent.

For every user request, first determine what evidence is required
to consider the task complete.

Do not stop merely because a tool executed successfully.

A task is complete only when the user's requested outcome has
been achieved and there is enough evidence to support the answer.

Before taking unnecessary additional actions, check whether the
available evidence already satisfies the user's request.

You have access to these tools.

list_files:
Lists all files inside the Forge workspace.

read_file:
Reads a file inside the Forge workspace.
Argument:
- filename: path relative to the workspace

write_file:
Writes or replaces a file inside the Forge workspace.
Arguments:
- filename: path relative to the workspace
- content: complete contents to write

run_command:
Runs a shell command from inside the Forge workspace.
Arguments:
- command: command to execute

search_files:
Searches the contents of files inside the Forge workspace for a text query.
Argument:
- query: text to search for

TOOL PROTOCOL:

You may REQUEST tools. You do not execute tools yourself.

When you need a tool, your entire response must contain ONLY one valid JSON object in exactly this form:

{
    "tool": "tool_name",
    "arguments": {
        "argument_name": "value"
    }
}

Do not include Markdown fences, explanations, headings, "Tool:", "Status:",
"Result:", or any other text around the JSON.

NEVER fabricate, predict, simulate, or invent the result of a tool call.

NEVER claim that a tool succeeded or failed. Only the Forge runtime executes tools
and provides authoritative tool results.

NEVER invent file contents, command output, search results, test results, web results,
or any other external evidence.

If information required to answer the user's request exists in the workspace and has
not been provided by the Forge runtime during this task, request the appropriate tool.

Do not ask the user to manually provide workspace files that you can access through
the available tools.

After the Forge runtime returns a tool result, either:
1. request exactly one additional tool using the JSON format above, or
2. give a final answer if the task is complete.

A workspace-related task is not complete until the necessary evidence has been
obtained through actual Forge tool results.

For example, if the user asks:

"Find all places where factorial is used, inspect the relevant files, and tell me whether the implementation and tests agree."

You must:
1. Search the workspace for "factorial".
2. Read the relevant implementation and test files.
3. Run the relevant tests.
4. Use those actual results to formulate the answer.
5. Do not inspect unrelated files.

Example:

{
  "tool": "list_files",
  "arguments": {}
}

Another example:

{
  "tool": "read_file",
  "arguments": {
    "filename": "factorial.py"
  }
}

Another example:

{
  "tool": "write_file",
  "arguments": {
    "filename": "example.py",
    "content": "print('hello')"
  }
}

Another example:
Example:

{
  "tool": "run_command",
  "arguments": {
    "command": "python hello.py"
  }
}

Another example:

{
  "tool": "search_files",
  "arguments": {
    "query": "factorial"
  }
}

Do not invent files.
Do not access files outside the workspace.

Tool-use rules:

- If the user's request requires information about files, code, tests, or commands in the Forge workspace, you MUST use the available tools to obtain that information.
- Never answer a workspace-related question from memory, assumptions, or hypothetical code.
- If the user asks where something is used, first use search_files to find actual occurrences.
- If the user asks you to inspect a file, you MUST use read_file on that file.
- If the user asks whether code works or whether implementation and tests agree, inspect the relevant files and run the relevant tests when possible.
- Do not invent file contents, test results, tool results, or file relationships.
- Do not describe a tool call without actually making the tool call.
- Use only the tools necessary to complete the request.
- After receiving a tool result, decide whether another tool is necessary.
- Continue using tools when the current evidence is insufficient to answer the request.
- Stop using tools once you have enough evidence to answer the request.
- Once the request is fully satisfied, respond with a normal natural-language answer.
- Do not call unrelated tools.
- Do not claim tests passed unless you actually ran them and received a successful result.
- Do not claim to have inspected a file unless you actually used read_file on it.

Task completion rules:

- Do not stop after identifying what needs to be done.
- If the user's request contains multiple required actions, complete ALL of them before giving the final answer.
- A statement such as "we need to inspect the file" or "we need to run the tests" is not completion.
- If another tool call is required to satisfy the user's request, make that tool call.
- Only provide the final natural-language answer after all required evidence has been collected.
- For a request to compare implementation and tests:
  1. Search for relevant usages.
  2. Read the implementation.
  3. Read the relevant tests.
  4. Run the tests.
  5. Compare the actual implementation, actual tests, and actual test result.
  6. Then provide the conclusion.

When completing a workspace task, continue using tools until you have all information required to answer the user's request.

When you have enough evidence to satisfy the user's request,
respond normally and do not request another tool.

When you still need evidence, use an appropriate tool.

For example, for:
"Find all places where factorial is used, inspect the relevant files, and tell me whether the implementation and tests agree."

You must:
- search for factorial
- read the relevant implementation
- read the relevant tests
- run the tests
- compare the implementation, tests, and actual test result
- then answer the user

Do not answer before completing these steps."""
    }
]


def ask_model():
    response = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": "qwen2.5-coder:3b",
            "messages": messages,
            "stream": False
        },
        timeout=120
    )

    response.raise_for_status()

    return response.json()["message"]["content"]

def define_success(user_request):
    request = user_request.lower()

    required_evidence = []

    if "what files" in request or "list files" in request:
        required_evidence.append("workspace file listing")

    if "search" in request or "find all" in request or "where" in request:
        required_evidence.append("search results")

    if "inspect" in request or "contents" in request or "inside" in request:
        required_evidence.append("relevant file contents")

    if "test" in request or "tests" in request or "agree" in request:
        required_evidence.append("test execution result")

    if "run" in request or "execute" in request:
        required_evidence.append("command execution result")

    return {
        "goal": user_request,
        "required_evidence": required_evidence
    }

def is_test_command(command):
    """Recognize explicit test-runner invocations, not incidental output text."""
    command = command.strip().lower()
    return bool(re.search(
        r"(?:^|[;&|]\s*|\s)(?:pytest|py\s+-m\s+(?:pytest|unittest)|python(?:3)?\s+-m\s+(?:pytest|unittest)|python(?:3)?\s+[^\s]*test[^\s]*\.py)(?:\s|$)",
        command,
    ))


def record_evidence(evidence, tool_name, arguments, result):
    evidence_record = {
        "tool": tool_name,
        "arguments": arguments.copy(),
        "result": result
    }

    if tool_name == "list_files":
        evidence["workspace file listing"].append(
            evidence_record
        )

    elif tool_name == "search_files":
        evidence["search results"].append(
            evidence_record
        )

    elif tool_name == "read_file":
        evidence["relevant file contents"].append(
            evidence_record
        )

    elif tool_name == "run_command":
        if not isinstance(result, dict):
            return

        if result.get("exit_code") != 0:
            return

        evidence["command execution result"].append(
            evidence_record
        )

        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")

        command = arguments.get("command", "")
        if is_test_command(command):
            evidence["test execution result"].append(
                evidence_record
            )

def check_success(success_definition, evidence):
    required = success_definition["required_evidence"]
    missing = []
    if not required:
        missing.append("task outcome not covered by completion rules")

    for requirement in required:
        if not evidence.get(requirement):
            missing.append(requirement)

    coverage = check_file_coverage(evidence)

    if "relevant file contents" in required:
        if coverage["missing_inspections"]:
             missing.append(
                "uninspected files: "
                + ", ".join(coverage["missing_inspections"])
            )

    return {
        "success": len(missing) == 0,
        "missing": missing
    }

def check_file_coverage(evidence):
    discovered = set()
    inspected = set()
    executed = set()

    for record in evidence["search results"]:
        results = record["result"]

        if not isinstance(results, list):
            continue

        for match in results:
            if not isinstance(match, str):
                continue

            filename = match.split(":", 1)[0]

            if filename.endswith(".py"):
                discovered.add(filename)

    for record in evidence["relevant file contents"]:
        filename = record["arguments"].get("filename")

        if filename:
            inspected.add(filename)

    for record in evidence["command execution result"]:
        command = record["arguments"].get("command", "")

        parts = command.strip().split()

        if (
            len(parts) == 2
            and parts[0] in ("python", "python3", "py")
            and parts[1].endswith(".py")
        ):
            executed.add(parts[1])

    return {
        "discovered": sorted(discovered),
        "inspected": sorted(inspected),
        "executed": sorted(executed),
        "missing_inspections": sorted(discovered - inspected),
        "missing_executions": sorted(discovered - executed)
    }

def run_tool_chain(answer, evidence, tool_history, step, max_steps):
    tool_call = parse_tool_call(answer)

    while tool_call and step < max_steps:
        step += 1

        call_signature = (
            tool_call.name,
            json.dumps(
                tool_call.arguments,
                sort_keys=True
            )
        )

        is_repeated = call_signature in tool_history

        if is_repeated:
            print(
                f"\n[REDUNDANT TOOL CALL] "
                f"{tool_call.name} {tool_call.arguments}"
            )

        tool_history.append(call_signature)

        try:
            validate_tool_call(tool_call)

            result = execute_tool(
                tool_call.name,
                tool_call.arguments
            )

            record_evidence(
                evidence,
                tool_call.name,
                tool_call.arguments,
                result
            )

            print(f"\n[Tool: {tool_call.name}]")
            print(result)

            tool_message = f"""Tool: {tool_call.name}

Status: success

Result:
{result}
"""

        except Exception as error:
            print(f"\n[Tool Error: {error}]")

            tool_message = f"""Tool: {tool_call.name}

Status: error

Error:
{error}
"""

        messages.append({
            "role": "assistant",
            "content": answer
        })

        messages.append({
            "role": "user",
            "content": tool_message
        })

        answer = ask_model()
        tool_call = parse_tool_call(answer)

    return answer, tool_call, step


def print_evidence_details(evidence):
    print("\n[Evidence Details]")

    for category, records in evidence.items():
        if not records:
            continue

        print(f"\n{category}:")

        for index, record in enumerate(records, start=1):
            print(f"  [{index}] tool: {record['tool']}")
            print(f"      arguments: {record['arguments']}")
            print(f"      result: {record['result']}")


def build_continuation_message(completion):
    missing = completion["missing"]

    message = """The task is not complete.

The Forge runtime has identified missing evidence:

"""

    for requirement in missing:
        if requirement.startswith("uninspected files: "):
            filenames = requirement.removeprefix(
                "uninspected files: "
            ).split(", ")

            message += "\nThe following files still need inspection:\n"

            for filename in filenames:
                message += f"- {filename}\n"

            message += (
                "\nUse the read_file tool to inspect these files. "
                "Request one tool call at a time.\n"
            )

        else:
            message += f"- {requirement}\n"

    message += """
Continue working on the original user request.

Do not repeat completed inspections.
Do not invent tool results.
Do not claim completion until the Forge runtime verifies the required evidence.
"""

    return message

def build_evidence_summary(evidence):
    lines = ["VERIFIED TOOL EVIDENCE"]

    for record in evidence.get("search results", []):
        lines.append(f"\nSEARCH QUERY: {record.get('arguments', {}).get('query', 'unknown')}")
        lines.append(f"Matches: {record['result']!r}")

    for record in evidence.get("workspace file listing", []):
        lines.append(f"\nWORKSPACE FILE LISTING: {record['result']!r}")

    for record in evidence["relevant file contents"]:
        filename = record["arguments"].get("filename", "unknown")
        content = record["result"]

        lines.append(f"\nSOURCE FILE: {filename}")
        lines.append(f"Contents:\n{content}")
        lines.append(
            "Note: source code describes possible behavior; "
            "it does not prove execution."
        )

    for record in evidence["command execution result"]:
        command = record["arguments"].get("command", "unknown")
        result = record["result"]

        lines.append(f"\nEXECUTED COMMAND: {command}")
        lines.append(f"Exit code: {result.get('exit_code')}")
        lines.append(f"Stdout: {result.get('stdout', '')!r}")
        lines.append(f"Stderr: {result.get('stderr', '')!r}")

    for record in evidence.get("test execution result", []):
        lines.append(f"\nTEST COMMAND: {record['arguments'].get('command', 'unknown')}")
        lines.append(f"Test runner output: {record['result']!r}")

    lines.append(
        "\nA successful command exit does not by itself prove "
        "that tests passed."
    )

    return "\n".join(lines)

def generate_grounded_answer(user_request, evidence, completion):
    summary = build_evidence_summary(evidence)

    status = (
        "COMPLETE"
        if completion["success"]
        else "INCOMPLETE"
    )

    grounding_prompt = f"""You are writing Forge's final response.

ORIGINAL USER REQUEST:
{user_request}

RUNTIME COMPLETION STATUS:
{status}

MISSING EVIDENCE:
{completion["missing"]}

VERIFIED EVIDENCE:
{summary}

INSTRUCTIONS:
- Answer the original user request using only the verified evidence.
- Distinguish source code from observed execution.
- Do not claim a function ran merely because it is defined.
- Do not claim tests passed unless test results explicitly support that.
- Do not invent files, commands, output, or observations.
- If completion is INCOMPLETE, clearly state what remains unverified.
- Do not request tools or produce JSON tool calls.
- Give a concise, useful final explanation.
"""

    messages.append({
        "role": "user",
        "content": grounding_prompt
    })

    try:
        return ask_model()
    finally:
        messages.pop()

# ---------- AGENT LOOP ----------

def run_task(prompt, max_steps=20, max_stalls=3):
    """Run one user task. Stop on verified completion, tool budget, or stalled progress."""
    success_definition = define_success(prompt)
    evidence = {
        "search results": [],
        "relevant file contents": [],
        "test execution result": [],
        "command execution result": [],
        "workspace file listing": []
    }
    tool_history = []
    print("\n[Success Definition]")
    print(success_definition)
    messages.append({"role": "user", "content": prompt})

    step = 0
    stalls = 0
    completion = check_success(success_definition, evidence)
    while step < max_steps:
        before = sum(map(len, evidence.values()))
        try:
            answer = ask_model()
            print("\nQwen:", answer)
            answer, tool_call, step = run_tool_chain(
                answer, evidence, tool_history, step, max_steps
            )
        except Exception as error:
            print(f"[Model or tool-loop error] {error}")
            break

        completion = check_success(success_definition, evidence)
        print("\n[Completion Check]")
        if completion["success"]:
            print("[OK] SUCCESS - required evidence collected")
            break
        for item in completion["missing"]:
            print(f"  [INCOMPLETE] {item}")
        if step >= max_steps:
            print("[STOP] Maximum tool steps reached.")
            break

        after = sum(map(len, evidence.values()))
        if after > before:
            stalls = 0
        else:
            stalls += 1
        if stalls >= max_stalls:
            print("[STOP] No evidence progress across repeated continuations.")
            break
        # Each continuation asks the model to obtain the exact missing evidence.
        # A productive iteration does not consume the stall budget.
        messages.append({"role": "assistant", "content": answer})
        messages.append({
            "role": "user", "content": build_continuation_message(completion)
        })

    print("\n[Tool History]")
    print(f"Total calls: {len(tool_history)}")
    print(f"Unique calls: {len(set(tool_history))}")
    print_evidence_details(evidence)
    coverage = check_file_coverage(evidence)
    print("\n[File Coverage]")
    for key, value in coverage.items():
        print(f"{key}: {value}")

    print("\n[Grounded Final Answer]")
    try:
        answer = generate_grounded_answer(prompt, evidence, completion)
        # Tool-shaped final responses are not user-facing answers.
        if parse_tool_call(answer):
            raise ValueError("Model returned a tool call instead of an explanation")
    except Exception as error:
        print(f"[Grounding unavailable] {error}")
        answer = (
            "Forge could not generate a grounded explanation.\n"
            f"Completion verified: {completion['success']}\n"
            f"Missing evidence: {completion['missing']}\n\n"
            + build_evidence_summary(evidence)
        )
    if not completion["success"]:
        answer = (
            "[INCOMPLETE — not verified]\n"
            + "Missing: " + ", ".join(completion["missing"]) + "\n\n"
            + answer
        )
    messages.append({"role": "assistant", "content": answer})
    print("\nForge:", answer)
    return {"completion": completion, "evidence": evidence, "answer": answer,
            "steps": step, "tool_history": tool_history}


def main():
    print("Forge started. Type 'exit' to quit.\n")
    while True:
        try:
            prompt = input("Forge > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if prompt.strip().lower() == "exit":
            break
        if not prompt.strip():
            continue
        run_task(prompt)


if __name__ == "__main__":
    main()
