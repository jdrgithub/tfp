#!/usr/bin/env python3
"""
Terraform Provider Viewer
=========================

Purpose
-------
This script helps you explore a Terraform provider without digging
through web pages or huge JSON files by hand.

You can:

1. List every resource and data source in a provider.
2. Search resources / data sources by a keyword (for example: ``ec2``).
3. Look at one exact resource / data source (for example: ``aws_instance``).
4. Choose what to see for that resource / data source:

   * ``all``        -- every argument
   * ``required``   -- only arguments you MUST set
   * ``optional``   -- only arguments you MAY set
   * ``attributes`` -- only values Terraform gives back to you (computed)

Where does the data come from?
------------------------------
Terraform itself can print the whole provider schema as JSON:

    terraform providers schema -json

This script runs that command for you in the current folder, reads the
JSON, and prints a small, human-friendly part of it.

That means:

* You should run this script inside a folder that contains Terraform
  code (a ``.tf`` file) that already uses the provider you want.
* You usually need to have run ``terraform init`` in that folder first.

No Terraform installed? No problem while you are learning.
This script has a tiny fake provider built in, just for practice:

    python3 tf_provider_viewer.py --demo list
    python3 tf_provider_viewer.py --demo search fake
    python3 tf_provider_viewer.py --demo show fake_server --show required
    python3 tf_provider_viewer.py --self-test

Real examples (inside a real Terraform project)
------------------------------------------------
    python3 tf_provider_viewer.py list
    python3 tf_provider_viewer.py list --kind resources
    python3 tf_provider_viewer.py list --kind datasources

    python3 tf_provider_viewer.py search ec2
    python3 tf_provider_viewer.py search bucket --show optional

    python3 tf_provider_viewer.py show aws_instance
    python3 tf_provider_viewer.py show aws_instance --show required
    python3 tf_provider_viewer.py show aws_instance --show optional
    python3 tf_provider_viewer.py show aws_instance --show attributes
    python3 tf_provider_viewer.py show aws_ami --kind datasource --show all

    # Bonus: if you saved the JSON yourself, you can read that file instead
    # of asking Terraform to produce it again:
    terraform providers schema -json > schema.json
    python3 tf_provider_viewer.py --schema-file schema.json list

Key Terraform words used in this script
---------------------------------------
Resource:
    A thing Terraform creates / manages, e.g. ``aws_instance``.

Data source (datasource):
    A thing Terraform only reads, e.g. ``aws_ami``.
    In the JSON, Terraform spells this ``data_source_schemas``.
    In this script we mostly use the shorter word ``datasource``.

Argument:
    A value YOU can write inside the resource block in your ``.tf`` file.
    Arguments are either ``required`` or ``optional``.

Attribute:
    In this script, "attribute" means a value Terraform gives back to
    you after it reads / creates the thing.  In the JSON this is called
    ``computed``.  Some values are both optional AND computed: you may
    set them, and Terraform may also fill them in for you.

Block:
    A nested group of arguments, e.g. ``ebs_block_device { ... }``
    inside ``aws_instance``.  In the JSON these are ``block_types``.

Only Python's standard library is used, so there is nothing to install.
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
# Python comes with a big "standard library" of ready-made tools.
# We only import the few tools this one script needs.

import argparse  # Reads command-line arguments like:  search ec2 --show required
import json  # Turns JSON text into Python dictionaries (and back again).
import subprocess  # Runs another program (Terraform) from inside Python.
import sys  # Gives us sys.exit(), and tells us how the script was started.
import unittest  # Helps us test our own small functions at the end of the file.
from dataclasses import dataclass  # A tidy way to bundle related data together.
from pathlib import Path  # A clean, modern way to work with file paths.


# ---------------------------------------------------------------------------
# Constants (values that never change while the script runs)
# ---------------------------------------------------------------------------
# By convention, constant names are written in ALL_CAPS.

# The four things a user may ask us to show for one resource / data source.
SHOW_CHOICES = ("all", "required", "optional", "attributes")

# The kinds of things a provider can contain.
# Note the spelling: humans say "datasource", Terraform JSON uses a longer key.
KIND_CHOICES = ("all", "resources", "datasources")

# Map our short, friendly kind names to the keys used in Terraform's JSON.
# This is a dictionary: look up a friendly name, get the JSON key.
KIND_TO_JSON_KEY = {
    "resources": "resource_schemas",
    "datasources": "data_source_schemas",
}

# Reverse the map above: look up a JSON key, get our friendly singular label.
# We build it with a small loop so it is clear exactly how it is made.
JSON_KEY_TO_LABEL = {}
for _kind, _json_key in KIND_TO_JSON_KEY.items():
    # "resources"[:-1] removes the last letter, giving "resource".
    JSON_KEY_TO_LABEL[_json_key] = _kind[:-1]


# ---------------------------------------------------------------------------
# A small "record" type for one resource or data source
# ---------------------------------------------------------------------------

@dataclass
class ProviderItem:
    """
    Everything we need to know about ONE resource or data source.

    A ``@dataclass`` is a class that mostly just holds data.
    Python writes the boring ``__init__`` method for us.

    Example of the data held here:
        name     = "aws_instance"
        kind     = "resource"          (or "datasource")
        provider = "aws"               (short provider name)
        block    = {...}               (the raw dictionary from the JSON)
    """

    name: str
    kind: str  # Either "resource" or "datasource".
    provider: str
    block: dict


# ---------------------------------------------------------------------------
# Loading the schema (getting the raw data)
# ---------------------------------------------------------------------------
# Each function below does ONE job.  Small, single-purpose functions are
# easier to read, easier to test, and easier to reuse.

def run_terraform_schema_command() -> dict:
    """
    Run ``terraform providers schema -json`` and return the parsed JSON.

    "Parsed" means: JSON text (a string) turned into Python dictionaries.

    Raises:
        RuntimeError: with a friendly message if Terraform cannot be run.
    """
    # The command is a list, not one long string.
    # Passing a list is the safe, recommended way to use subprocess.
    command = ["terraform", "providers", "schema", "-json"]

    try:
        # capture_output=True  -> do not print Terraform's output ourselves;
        #                         keep it in variables instead.
        # text=True            -> give us strings, not raw bytes.
        # check=False          -> we will check for errors ourselves below,
        #                         so we can print a friendlier message.
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        # This error means the "terraform" program was not found at all.
        # The `from None` part hides Python's noisy inner traceback so the
        # user sees one clear message instead of a wall of text.
        raise RuntimeError(
            "Could not find the 'terraform' program.\n"
            "Install Terraform, or try the built-in practice data:\n"
            "    python3 tf_provider_viewer.py --demo list\n"
            "    python3 tf_provider_viewer.py --self-test"
        ) from None

    # A return code of 0 means "success".  Anything else means "it failed".
    if result.returncode != 0:
        # Terraform writes its error messages to "stderr" (standard error).
        error_text = result.stderr.strip() or "Terraform gave no error text."
        raise RuntimeError(
            "Terraform could not produce a schema.\n"
            "Tips: run this inside a Terraform project folder, and run "
            "'terraform init' there first.\n"
            f"Terraform said:\n{error_text}"
        )

    # json.loads() turns a JSON *string* into Python dictionaries / lists.
    # ("loads" is short for "load string".)
    return json.loads(result.stdout)


def load_schema_from_file(schema_file: str) -> dict:
    """
    Load a schema that was saved earlier into a JSON file.

    Example of how such a file is created:
        terraform providers schema -json > schema.json
    """
    # Path() makes working with the file name easier and cross-platform.
    path = Path(schema_file)

    # Fail early, with a clear message, if the file simply is not there.
    if not path.is_file():
        raise RuntimeError(f"Schema file not found: {schema_file}")

    # encoding="utf-8" is the normal text encoding for JSON files.
    # json.loads() then turns the file's text into Python dictionaries.
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema(schema_file: str | None = None, use_demo: bool = False) -> dict:
    """
    Get the schema from exactly one place, in a clear order of preference.

    1. If ``use_demo`` is True, use the tiny fake schema in this file.
    2. Else, if a ``schema_file`` was given, read that file.
    3. Else, ask Terraform itself.

    The odd-looking type ``str | None`` means "a string, or nothing".
    It is how we say "this argument is optional".
    """
    if use_demo:
        return make_fake_schema()

    if schema_file:
        return load_schema_from_file(schema_file)

    return run_terraform_schema_command()


# ---------------------------------------------------------------------------
# Reading the schema (finding things inside the raw data)
# ---------------------------------------------------------------------------

def get_short_provider_name(provider_address: str) -> str:
    """
    Turn a long provider address into a short name.

    Example:
        "registry.terraform.io/hashicorp/aws"  ->  "aws"

    The address is split on "/" and we keep only the last piece.
    ``[-1]`` means "the last item in the list".
    """
    return provider_address.rsplit("/", maxsplit=1)[-1]


def collect_items(schema: dict, kind: str = "all") -> list[ProviderItem]:
    """
    Collect every resource and/or data source from the whole schema.

    Args:
        schema: The full dictionary returned by Terraform.
        kind:   "all", "resources", or "datasources".

    Returns:
        A list of ProviderItem objects, sorted by kind, then by name.
        Sorting makes the output stable and easy to scan.
    """
    items: list[ProviderItem] = []

    # .get("provider_schemas", {}) means:
    # "give me provider_schemas, or an empty dictionary if it is missing".
    # Using .get() avoids a crash (KeyError) on unexpected input.
    provider_schemas = schema.get("provider_schemas", {})

    # .items() lets us loop over both the key and the value of a dictionary.
    for provider_address, provider_data in provider_schemas.items():
        provider_name = get_short_provider_name(provider_address)

        # Decide which JSON sections we need to look inside.
        # If kind is "all", we need every section in our map.
        if kind == "all":
            json_keys = list(KIND_TO_JSON_KEY.values())
        else:
            json_keys = [KIND_TO_JSON_KEY[kind]]

        for json_key in json_keys:
            label = JSON_KEY_TO_LABEL[json_key]  # "resource" or "datasource"
            schemas_for_kind = provider_data.get(json_key, {})

            for name, item_data in schemas_for_kind.items():
                # In the JSON, the useful part lives under the "block" key.
                block = item_data.get("block", {})

                items.append(
                    ProviderItem(
                        name=name,
                        kind=label,
                        provider=provider_name,
                        block=block,
                    )
                )

    # Sort by kind first ("datasource" before "resource") and then by name.
    # A "lambda" is a tiny one-line function: here it builds the sort key.
    items.sort(key=lambda item: (item.kind, item.name))
    return items


def search_items(items: list[ProviderItem], keyword: str) -> list[ProviderItem]:
    """
    Return every item whose name contains ``keyword``.

    The search is case-insensitive, so "EC2", "Ec2" and "ec2" all match
    the same things.  ``.casefold()`` is Python's strongest way to make
    text lower-case for comparisons like this.
    """
    folded_keyword = keyword.casefold()

    # This is a "list comprehension": a short, Pythonic way to build a
    # new list by keeping only the items that pass a test.
    return [
        item
        for item in items
        if folded_keyword in item.name.casefold()
    ]


def find_exact_items(items: list[ProviderItem], name: str) -> list[ProviderItem]:
    """
    Return items whose name matches ``name`` exactly (ignoring case).

    A list is returned (not a single item) because the same name can
    exist as both a resource AND a data source.
    """
    folded_name = name.casefold()
    return [item for item in items if item.name.casefold() == folded_name]


def filter_items_by_kind(
    items: list[ProviderItem],
    kind: str | None,
) -> list[ProviderItem]:
    """
    Keep only resources, only data sources, or everything.

    ``kind`` here is the singular label stored on each item:
    "resource" or "datasource".  ``None`` means "do not filter".
    """
    if kind is None:
        return items
    return [item for item in items if item.kind == kind]


# ---------------------------------------------------------------------------
# Understanding one attribute / argument
# ---------------------------------------------------------------------------
# In the JSON, each attribute is a dictionary that may contain the keys:
#   "type"        e.g. "string" or ["list", "string"]
#   "required"    True if you MUST set it
#   "optional"    True if you MAY set it
#   "computed"    True if Terraform fills it in for you
#   "sensitive"   True if it is secret-like (e.g. a password)
#   "deprecated"  A message if you should stop using it
#   "description" Human-readable text from the provider authors

def is_required(attribute: dict) -> bool:
    """Return True if this attribute must be set by the user."""
    # .get("required", False) means: default to False when the key is absent.
    # bool(...) makes sure we always return a real True / False value.
    return bool(attribute.get("required", False))


def is_optional(attribute: dict) -> bool:
    """Return True if this attribute may be set by the user."""
    return bool(attribute.get("optional", False))


def is_computed(attribute: dict) -> bool:
    """Return True if Terraform fills this value in for you."""
    return bool(attribute.get("computed", False))


def attribute_matches_show(attribute: dict, show: str) -> bool:
    """
    Decide whether one attribute should be printed for a ``show`` choice.

    * "all"        -> every attribute
    * "required"   -> only required attributes
    * "optional"   -> only optional attributes
    * "attributes" -> only computed attributes (values given back to you)
    """
    if show == "all":
        return True
    if show == "required":
        return is_required(attribute)
    if show == "optional":
        return is_optional(attribute)
    if show == "attributes":
        return is_computed(attribute)

    # argparse should never let an unknown value reach this point.
    # Raising a clear error is better than silently printing nothing.
    raise ValueError(f"Unknown --show value: {show!r}")


def filter_attributes(attributes: dict, show: str) -> dict:
    """
    Return a NEW dictionary containing only the attributes we want.

    The original dictionary is never changed.  Working on a copy is a
    safe habit: other code may still need the full data.
    """
    # This is a "dictionary comprehension": build a new dictionary by
    # keeping only the key/value pairs that pass our test.
    return {
        name: attribute
        for name, attribute in attributes.items()
        if attribute_matches_show(attribute, show)
    }


def format_type(type_value: object) -> str:
    """
    Turn Terraform's JSON type description into readable text.

    Terraform encodes types in two shapes:

    * Simple:  "string", "number", "bool"
    * Nested:  ["list", "string"]            -> "list of string"
               ["object", {"id": "string"}]  -> "object({id = string})"

    This function calls ITSELF for the inner part.  A function that
    calls itself like this is called "recursive".
    """
    # Shape 1: a plain string is already readable.
    if isinstance(type_value, str):
        return type_value

    # Shape 2: a list whose first item names the outer type.
    if isinstance(type_value, list) and type_value:
        outer_type = type_value[0]

        # Some types have no inner part, e.g. just ["dynamic"].
        if len(type_value) == 1:
            return str(outer_type)

        inner_value = type_value[1]

        # An object lists named fields, e.g. {"id": "string", "on": "bool"}.
        if outer_type == "object" and isinstance(inner_value, dict):
            fields = ", ".join(
                f"{field_name} = {format_type(field_type)}"
                for field_name, field_type in inner_value.items()
            )
            return f"object({{{fields}}})"

        # list / set / map / tuple all wrap one inner type.
        return f"{outer_type} of {format_type(inner_value)}"

    # Anything unexpected: fall back to Python's normal text form.
    return str(type_value)


def describe_attribute_flags(attribute: dict) -> str:
    """
    Build a short label like:  "required, sensitive"

    This gives the reader the important facts on one small line.
    """
    flags: list[str] = []

    # The order here is deliberate: most important fact first.
    if is_required(attribute):
        flags.append("required")
    if is_optional(attribute):
        flags.append("optional")
    if is_computed(attribute):
        flags.append("computed")
    if attribute.get("sensitive"):
        flags.append("sensitive")
    if attribute.get("deprecated"):
        flags.append("deprecated")

    # ", ".join(flags) sticks the pieces together with a comma and space.
    # If there are no flags at all, we say so plainly.
    return ", ".join(flags) if flags else "no flags"


# ---------------------------------------------------------------------------
# Deciding whether nested blocks should be printed
# ---------------------------------------------------------------------------

def block_type_matches_show(block_type: dict, show: str) -> bool:
    """
    Decide whether one nested block should be printed for ``show``.

    A nested block is required when Terraform demands at least one of
    them.  In the JSON that is ``min_items`` being 1 or more.
    """
    # A block_type wraps the real block, e.g.
    # {"nesting_mode": "list", "min_items": 1, "block": {...}}
    min_items = block_type.get("min_items", 0)

    if show == "all":
        return True
    if show == "required":
        return min_items >= 1
    if show == "optional":
        return min_items < 1
    if show == "attributes":
        # In "attributes" mode we only care about blocks that contain
        # computed values somewhere inside them.
        return block_contains_computed(block_type.get("block", {}))

    raise ValueError(f"Unknown --show value: {show!r}")


def block_contains_computed(block: dict) -> bool:
    """
    Return True if a block (or any block nested inside it) is computed.

    This is recursive again: we check this level, then ask the same
    question about every deeper level.
    """
    # First, check the plain attributes at this level.
    for attribute in block.get("attributes", {}).values():
        if is_computed(attribute):
            return True

    # Then, check every nested block, one level deeper each time.
    for block_type in block.get("block_types", {}).values():
        if block_contains_computed(block_type.get("block", {})):
            return True

    # Nothing computed anywhere inside.
    return False


# ---------------------------------------------------------------------------
# Printing (turning data into text a human can read)
# ---------------------------------------------------------------------------

def print_items_list(items: list[ProviderItem], title: str) -> None:
    """
    Print a numbered, grouped list of resources / data sources.

    Numbering matters: after a search, the user can type a number to
    choose one of the matches.
    """
    print(title)
    print("-" * len(title))

    if not items:
        print("  (nothing found)")
        return

    # enumerate(..., start=1) counts 1, 2, 3, ... while looping.
    # Humans prefer starting at 1; computers often start at 0.
    for number, item in enumerate(items, start=1):
        print(f"{number:>3}. [{item.kind}] {item.name}  (provider: {item.provider})")

    # An f-string (f"...") places values inside text using {curly braces}.
    print(f"\nTotal: {len(items)}")


def print_one_attribute(name: str, attribute: dict, indent: str) -> None:
    """Print exactly one attribute, with its type, flags and description."""
    type_text = format_type(attribute.get("type", "unknown"))
    flags_text = describe_attribute_flags(attribute)

    # Main line, e.g. "  - instance_type : string  (optional)"
    print(f"{indent}- {name} : {type_text}  ({flags_text})")

    # Optional extra lines.  We only print them when they exist.
    description = attribute.get("description")
    if description:
        print(f"{indent}    {description}")

    deprecated = attribute.get("deprecated")
    if deprecated:
        print(f"{indent}    Deprecated: {deprecated}")


def print_block(block: dict, show: str, indent_level: int = 0) -> None:
    """
    Print the attributes and nested blocks of ONE block, recursively.

    ``indent_level`` controls how far the text is pushed to the right.
    Deeper nesting = more indentation = easier to see the structure.
    """
    # Two spaces per level.  " " * 4 means four spaces, and so on.
    indent = "  " * indent_level

    # --- Plain attributes at this level --------------------------------
    all_attributes = block.get("attributes", {})
    visible_attributes = filter_attributes(all_attributes, show)

    if visible_attributes:
        print(f"{indent}Arguments / attributes:")
        # Sorting the names alphabetically makes the output predictable.
        for name in sorted(visible_attributes):
            print_one_attribute(name, visible_attributes[name], indent + "  ")
    elif indent_level == 0:
        # Only say this at the top level, so nested output stays quiet.
        print(f"{indent}(no entries match --show {show})")

    # --- Nested blocks at this level -----------------------------------
    all_block_types = block.get("block_types", {})

    # Keep only the blocks that match the current --show choice.
    visible_block_types = {
        name: block_type
        for name, block_type in all_block_types.items()
        if block_type_matches_show(block_type, show)
    }

    if not visible_block_types:
        return

    print(f"{indent}Nested blocks:")

    for name in sorted(visible_block_types):
        block_type = visible_block_types[name]
        nesting_mode = block_type.get("nesting_mode", "single")
        min_items = block_type.get("min_items", 0)
        max_items = block_type.get("max_items")

        # Build a short note like:  (list, min: 1, max: 3)
        limits = f"min: {min_items}"
        if max_items is not None:
            limits += f", max: {max_items}"
        print(f"{indent}  - {name} : block ({nesting_mode}, {limits})")

        description = block_type.get("description")
        if description:
            print(f"{indent}      {description}")

        # The magic step: print the INSIDE of this block by calling
        # print_block() again, one level deeper.
        nested_block = block_type.get("block", {})
        print_block(nested_block, show, indent_level + 2)


def print_item_details(item: ProviderItem, show: str) -> None:
    """Print a clear heading, then the chosen details for one item."""
    # A line of "=" characters, as long as the heading text.
    heading = f"{item.name}  [{item.kind}]  (provider: {item.provider})"
    print()
    print(heading)
    print("=" * len(heading))
    print(f"Showing: {show}")
    print()

    print_block(item.block, show)


# ---------------------------------------------------------------------------
# Asking the user to choose from a list
# ---------------------------------------------------------------------------

def choose_item_from_list(items: list[ProviderItem]) -> ProviderItem | None:
    """
    Ask the user to pick one item by typing its number.

    Returns the chosen item, or None if the user cancels.
    """
    # With only one match there is nothing to choose.  Use it directly.
    if len(items) == 1:
        return items[0]

    print("\nType a number to view one item, or 'q' to quit.")

    # A `while True` loop runs until we `return` or `break` out of it.
    # That is the right shape for "keep asking until the answer is valid".
    while True:
        try:
            answer = input("Your choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            # EOFError: input ended (e.g. text was piped into the script).
            # KeyboardInterrupt: the user pressed Ctrl+C.
            print("\nCancelled.")
            return None

        # Allow a polite exit.
        if answer.casefold() in {"q", "quit", "exit", ""}:
            return None

        # .isdigit() checks the text is a whole number like "3".
        if not answer.isdigit():
            print("Please type a number from the list, or 'q' to quit.")
            continue  # Go back to the top of the loop and ask again.

        number = int(answer)

        # Valid numbers run from 1 up to the number of items.
        if 1 <= number <= len(items):
            # Python lists start at 0, so choice 1 lives at index 0.
            return items[number - 1]

        print(f"Please choose a number between 1 and {len(items)}.")


# ---------------------------------------------------------------------------
# Command handlers (one function per command)
# ---------------------------------------------------------------------------
# argparse will send each command to its own small handler function.
# That keeps `main()` short and easy to follow.

def handle_list(items: list[ProviderItem], kind: str) -> int:
    """Handle:  list [--kind all|resources|datasources]"""
    # Reuse collect-style filtering: `kind` arrives as a plural word,
    # while each item stores a singular label, so we filter manually here.
    if kind == "resources":
        visible = [item for item in items if item.kind == "resource"]
    elif kind == "datasources":
        visible = [item for item in items if item.kind == "datasource"]
    else:  # "all"
        visible = items

    print_items_list(visible, f"Listing: {kind}")
    return 0  # 0 means "the program succeeded" (a Unix convention).


def handle_search(
    items: list[ProviderItem],
    keyword: str,
    show: str | None,
) -> int:
    """
    Handle:  search KEYWORD [--show all|required|optional|attributes]

    Without ``--show``, this only lists the matches.
    With ``--show``, the user picks a match and we display its details.
    """
    matches = search_items(items, keyword)

    if not matches:
        print(f"No resources or data sources match: {keyword!r}")
        return 0

    print_items_list(matches, f"Search results for: {keyword!r}")

    # If the user did not ask to view details, our work is done.
    if show is None:
        print("\nTip: add --show required (for example) to view one of these.")
        return 0

    chosen = choose_item_from_list(matches)
    if chosen is None:
        return 0

    print_item_details(chosen, show)
    return 0


def handle_show(
    items: list[ProviderItem],
    name: str,
    kind: str | None,
    show: str,
) -> int:
    """
    Handle:  show NAME [--kind resource|datasource] [--show ...]

    ``NAME`` must match exactly, e.g. ``aws_instance``.
    If nothing matches exactly, we fall back to a keyword search so the
    user still gets helpful suggestions instead of a dead end.
    """
    exact_matches = filter_items_by_kind(find_exact_items(items, name), kind)

    if exact_matches:
        # The same name can exist as a resource AND a data source.
        # In that case, show each one, clearly labelled.
        for item in exact_matches:
            print_item_details(item, show)
        return 0

    # No exact match: be helpful and offer the closest keyword matches.
    print(f"No exact resource or data source named: {name!r}")
    suggestions = filter_items_by_kind(search_items(items, name), kind)

    if not suggestions:
        print("No similar names were found either. Try: list")
        return 0

    print("\nDid you mean one of these?")
    print_items_list(suggestions, "Similar names")

    chosen = choose_item_from_list(suggestions)
    if chosen is not None:
        print_item_details(chosen, show)
    return 0


# ---------------------------------------------------------------------------
# Building the command-line interface
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """
    Build the argparse parser that understands our command line.

    Keeping this in its own function means `main()` stays tiny, and
    tests can build a parser without running the whole program.
    """
    parser = argparse.ArgumentParser(
        description="View Terraform provider resources, data sources, "
                    "arguments and attributes.",
        epilog=(
            "Examples:\n"
            "  %(prog)s list\n"
            "  %(prog)s list --kind datasources\n"
            "  %(prog)s search ec2\n"
            "  %(prog)s search bucket --show optional\n"
            "  %(prog)s show aws_instance --show required\n"
            "  %(prog)s show aws_ami --kind datasource --show attributes\n"
            "  %(prog)s --demo show fake_server --show all\n"
            "  %(prog)s --self-test\n"
        ),
        # RawDescriptionHelpFormatter keeps our line breaks in the help text.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global options.  These go BEFORE the command word, e.g.
    #   python3 tf_provider_viewer.py --demo list
    parser.add_argument(
        "--schema-file",
        metavar="PATH",
        help="Read a saved schema JSON file instead of running Terraform.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",  # A flag: present = True, absent = False.
        help="Use the tiny built-in fake provider (no Terraform needed).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the built-in tests at the end of this file, then exit.",
    )

    # Sub-commands: each command word ("list", "search", "show") gets its
    # own small set of options.  This is called a "subparser" in argparse.
    subparsers = parser.add_subparsers(dest="command")

    # --- Command: list --------------------------------------------------
    list_parser = subparsers.add_parser(
        "list",
        help="List all resources and/or data sources.",
    )
    list_parser.add_argument(
        "--kind",
        choices=KIND_CHOICES,
        default="all",
        help="What to list (default: all).",
    )

    # --- Command: search ------------------------------------------------
    search_parser = subparsers.add_parser(
        "search",
        help="Find resources / data sources whose name contains a keyword.",
    )
    search_parser.add_argument(
        "keyword",
        help="Text to search for, e.g. ec2 or bucket.",
    )
    search_parser.add_argument(
        "--show",
        choices=SHOW_CHOICES,
        default=None,  # None means: only list matches, do not open one.
        help="After listing, open one match and show this view of it.",
    )

    # --- Command: show --------------------------------------------------
    show_parser = subparsers.add_parser(
        "show",
        help="Show the arguments / attributes of one exact name.",
    )
    show_parser.add_argument(
        "name",
        help="Exact name, e.g. aws_instance.",
    )
    show_parser.add_argument(
        "--kind",
        choices=("resource", "datasource"),
        default=None,
        help="Only look at a resource, or only at a data source.",
    )
    show_parser.add_argument(
        "--show",
        choices=SHOW_CHOICES,
        default="all",
        help="What to show (default: all).",
    )

    return parser


# ---------------------------------------------------------------------------
# main(): the starting point of the program
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """
    Run the whole program and return an exit code (0 = success).

    ``argv`` is normally None, meaning "read the real command line".
    Tests can pass their own list instead, which is very handy.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # The self-test needs no schema at all, so handle it first.
    if args.self_test:
        return run_self_tests()

    # With no command word, show the help text and stop politely.
    # This is friendlier than crashing, especially for a first-time user.
    if args.command is None:
        parser.print_help()
        return 0

    # Load the data.  Loading can fail (no Terraform, missing file, ...),
    # so we catch our friendly RuntimeError and print just its message.
    try:
        schema = load_schema(schema_file=args.schema_file, use_demo=args.demo)
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1  # Any non-zero code means "the program failed".

    # Turn the raw JSON into a tidy list once.  Every command then works
    # with the same list, instead of re-reading the JSON each time.
    items = collect_items(schema)

    if not items:
        print(
            "No resources or data sources were found in this schema.\n"
            "Tips: check that you are in a Terraform project folder, "
            "that the provider is used by the code there, and that you "
            "have run 'terraform init'."
        )
        return 0

    # Send the work to the one handler that matches the command.
    # Each handler returns its own exit code, which we pass straight on.
    if args.command == "list":
        return handle_list(items, kind=args.kind)

    if args.command == "search":
        return handle_search(items, keyword=args.keyword, show=args.show)

    if args.command == "show":
        return handle_show(
            items,
            name=args.name,
            kind=args.kind,
            show=args.show,
        )

    # argparse restricts the command to the choices above, so reaching
    # this line would mean a bug in our own code.  Show help, to be safe.
    parser.print_help()
    return 0


# ===========================================================================
# TESTS
# ===========================================================================
# Everything below this line exists only to CHECK that the functions above
# work correctly.  None of it is needed during normal use.
#
# Run the tests with:
#     python3 tf_provider_viewer.py --self-test
#
# How to read these tests:
# * Each test is a small method whose name starts with "test_".
# * Each test builds a tiny, known input, runs ONE function, and checks
#   the result with self.assert... methods.
# * If a check fails, unittest prints exactly which test failed and why.
#
# We use a FAKE schema instead of real Terraform output so the tests work
# on any computer -- even one with no Terraform installed at all.

def make_fake_schema() -> dict:
    """
    Build a tiny fake Terraform schema for the demo and for the tests.

    It deliberately contains the interesting cases:

    * a resource with required, optional and computed values,
    * a nested block with its own required value inside it,
    * a data source,
    * and a second resource, so searches can find more than one match.
    """
    return {
        "format_version": "1.0",
        "provider_schemas": {
            "registry.terraform.io/hashicorp/fake": {
                "provider": {"version": 0},
                "resource_schemas": {
                    "fake_server": {
                        "version": 0,
                        "block": {
                            "attributes": {
                                # Required: the user MUST set this.
                                "name": {
                                    "type": "string",
                                    "required": True,
                                    "description": "The name of the server.",
                                },
                                # Optional: the user MAY set this.
                                "size": {
                                    "type": "string",
                                    "optional": True,
                                    "description": "How big the server is.",
                                },
                                # Optional AND computed: either side may set it.
                                "tags": {
                                    "type": ["map", "string"],
                                    "optional": True,
                                    "computed": True,
                                },
                                # Computed only: Terraform gives this back.
                                "id": {
                                    "type": "string",
                                    "computed": True,
                                },
                                "ip_address": {
                                    "type": "string",
                                    "computed": True,
                                },
                            },
                            "block_types": {
                                # A nested block with a required value inside.
                                "disk": {
                                    "nesting_mode": "list",
                                    "min_items": 1,
                                    "block": {
                                        "attributes": {
                                            "gb": {
                                                "type": "number",
                                                "required": True,
                                            },
                                            "label": {
                                                "type": "string",
                                                "optional": True,
                                            },
                                        }
                                    },
                                }
                            },
                        },
                    },
                    "fake_bucket": {
                        "version": 0,
                        "block": {
                            "attributes": {
                                "bucket_name": {
                                    "type": "string",
                                    "required": True,
                                },
                                "id": {
                                    "type": "string",
                                    "computed": True,
                                },
                            }
                        },
                    },
                },
                "data_source_schemas": {
                    "fake_image": {
                        "version": 0,
                        "block": {
                            "attributes": {
                                "name_filter": {
                                    "type": "string",
                                    "required": True,
                                },
                                "id": {
                                    "type": "string",
                                    "computed": True,
                                },
                            }
                        },
                    }
                },
            }
        },
    }


class TestFormatType(unittest.TestCase):
    """Tests for format_type(): turning JSON types into readable text."""

    def test_simple_type_is_unchanged(self):
        # A plain type name should pass straight through.
        self.assertEqual(format_type("string"), "string")

    def test_list_type_names_its_inner_type(self):
        # ["list", "string"] should read as "list of string".
        self.assertEqual(format_type(["list", "string"]), "list of string")

    def test_object_type_lists_its_fields(self):
        # Objects should spell out each named field and its type.
        result = format_type(["object", {"id": "string", "on": "bool"}])
        self.assertEqual(result, "object({id = string, on = bool})")


class TestCollectAndSearch(unittest.TestCase):
    """Tests for collect_items(), search_items() and find_exact_items()."""

    def setUp(self):
        # setUp() runs before EVERY test in this class.
        # It gives each test a fresh, identical starting point.
        self.items = collect_items(make_fake_schema())

    def test_collect_finds_resources_and_datasources(self):
        # Our fake schema has 2 resources and 1 data source.
        self.assertEqual(len(self.items), 3)

    def test_collect_uses_short_provider_name(self):
        # "registry.terraform.io/hashicorp/fake" should become "fake".
        providers = {item.provider for item in self.items}
        self.assertEqual(providers, {"fake"})

    def test_collect_can_keep_only_datasources(self):
        datasources = collect_items(make_fake_schema(), kind="datasources")
        self.assertEqual([item.name for item in datasources], ["fake_image"])

    def test_search_matches_part_of_a_name(self):
        # "fake" appears inside all three names.
        matches = search_items(self.items, "fake")
        self.assertEqual(len(matches), 3)

    def test_search_ignores_letter_case(self):
        # Searching in UPPER CASE should still find lower-case names.
        matches = search_items(self.items, "SERVER")
        self.assertEqual([item.name for item in matches], ["fake_server"])

    def test_search_with_no_match_returns_empty_list(self):
        self.assertEqual(search_items(self.items, "does_not_exist"), [])

    def test_find_exact_requires_the_whole_name(self):
        # "fake" alone must NOT count as an exact match...
        self.assertEqual(find_exact_items(self.items, "fake"), [])
        # ...but the full name must match.
        matches = find_exact_items(self.items, "fake_server")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].kind, "resource")


class TestFiltering(unittest.TestCase):
    """Tests for the required / optional / attributes filtering."""

    def setUp(self):
        # Grab the attributes of our fake_server resource, once per test.
        items = collect_items(make_fake_schema())
        server = find_exact_items(items, "fake_server")[0]
        self.attributes = server.block["attributes"]

    def test_required_keeps_only_required(self):
        result = filter_attributes(self.attributes, "required")
        self.assertEqual(sorted(result), ["name"])

    def test_optional_keeps_only_optional(self):
        result = filter_attributes(self.attributes, "optional")
        self.assertEqual(sorted(result), ["size", "tags"])

    def test_attributes_keeps_only_computed(self):
        # Note: "tags" appears here too, because it is optional AND computed.
        # That overlap is real Terraform behaviour, not a bug.
        result = filter_attributes(self.attributes, "attributes")
        self.assertEqual(sorted(result), ["id", "ip_address", "tags"])

    def test_all_keeps_everything(self):
        result = filter_attributes(self.attributes, "all")
        self.assertEqual(len(result), len(self.attributes))

    def test_unknown_show_value_is_an_error(self):
        # A misspelled view name should fail loudly, not print nothing.
        with self.assertRaises(ValueError):
            filter_attributes(self.attributes, "everything")

    def test_block_contains_computed(self):
        # fake_server has computed values at its top level.
        items = collect_items(make_fake_schema())
        server = find_exact_items(items, "fake_server")[0]
        self.assertTrue(block_contains_computed(server.block))

    def test_required_block_type_matches_required_view(self):
        # The "disk" block has min_items=1, so it is a required block.
        disk_block_type = {"nesting_mode": "list", "min_items": 1, "block": {}}
        self.assertTrue(block_type_matches_show(disk_block_type, "required"))
        self.assertFalse(block_type_matches_show(disk_block_type, "optional"))


def run_self_tests() -> int:
    """
    Run every test class above and return an exit code.

    Returns 0 when all tests pass, and 1 when any test fails -- the same
    convention used by main(), so the shell can tell success from failure.
    """
    # Collect the test classes into a "suite" (a bundle of tests).
    # Listing them explicitly keeps it obvious which tests will run.
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for test_class in (TestFormatType, TestCollectAndSearch, TestFiltering):
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    # verbosity=2 prints one line per test, so you can see each result.
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # .wasSuccessful() is True only when every single test passed.
    return 0 if result.wasSuccessful() else 1


# ---------------------------------------------------------------------------
# Program entry point (this MUST stay at the very bottom of the file)
# ---------------------------------------------------------------------------
# When you run "python3 tf_provider_viewer.py ...", Python sets the special
# variable __name__ to "__main__", so the code below runs.
# When another file imports this one (as the tests could), __name__ is the
# file's name instead, and nothing runs automatically.  That is exactly
# what we want: importing should never start the program by itself.
#
# sys.exit(main()) hands our exit code back to the shell:
# 0 = success, anything else = something went wrong.

if __name__ == "__main__":
    sys.exit(main())
