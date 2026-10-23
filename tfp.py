#!/usr/bin/env python3
"""
Terraform Provider Viewer
=========================

Purpose
-------
This script helps you explore a Terraform provider without digging
through web pages or huge JSON files by hand.

You can:

1. List resources, data sources, ephemeral resources and functions.
2. Search by a keyword (for example: ``ec2``).
3. Look at one exact item (for example: ``aws_instance``), or a
   provider's own configuration (``provider aws``).
4. Choose what to see for that item:

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

    python3 tfp.py --demo list
    python3 tfp.py --demo search fake
    python3 tfp.py --demo show fake_server --show required
    python3 tfp.py --self-test

Real examples (inside a real Terraform project)
------------------------------------------------
    python3 tfp.py list
    python3 tfp.py list --kind resources
    python3 tfp.py list --kind datasources

    python3 tfp.py search ec2
    python3 tfp.py search bucket --show optional

    python3 tfp.py show aws_instance
    python3 tfp.py show aws_instance --show required
    python3 tfp.py show aws_instance --show optional
    python3 tfp.py show aws_instance --show attributes
    python3 tfp.py show aws_ami --kind datasource --show all

    # Bonus: if you saved the JSON yourself, you can read that file instead
    # of asking Terraform to produce it again:
    terraform providers schema -json > schema.json
    python3 tfp.py --schema-file schema.json list

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
import contextlib  # Redirects printed output in tests so we can check it.
import io  # In-memory text streams, used by the output tests at the end.
import json  # Turns JSON text into Python dictionaries (and back again).
import subprocess  # Runs another program (Terraform) from inside Python.
import sys  # Gives us sys.exit(), and tells us how the script was started.
import textwrap  # Wraps long text so table columns stay aligned.
import unittest  # Helps us test our own small functions at the end of the file.
from dataclasses import dataclass  # A tidy way to bundle related data together.
from pathlib import Path  # A clean, modern way to work with file paths.


# ---------------------------------------------------------------------------
# Constants (values that never change while the script runs)
# ---------------------------------------------------------------------------
# By convention, constant names are written in ALL_CAPS.

# Column headings for the attribute table, in display order.
# Flag columns use a short heading and an "X" marker (see FLAG_MARKER).
ATTRIBUTE_HEADERS = ("NAME", "TYPE", "REQ", "OPT", "COMP", "SENS", "DESCRIPTION")

# Shown in a flag column when the attribute has that property; blank otherwise.
FLAG_MARKER = "X"

# Legend printed above every attribute table, explaining the short headings.
FLAG_LEGEND = (
    "Markers: X = yes  "
    "(REQ = required, OPT = optional, COMP = computed, SENS = sensitive)"
)

# Only DESCRIPTION is capped and wrapped.  NAME and TYPE grow to fit the
# widest entry in the table so every row lines up.
MAX_DESCRIPTION_WIDTH = 46

# Plain-language explanations for block names whose raw provider schema
# is often unclear.  These supplement (or stand in for) the description
# supplied by the provider, which for these blocks is often missing.
BLOCK_PURPOSE_EXPLANATIONS = {
    "filter": (
        "Narrows the data-source lookup using provider filter names, "
        "such as \"tag:Name\" or \"vpc-id\". "
        "This does not change the object itself."
    ),
    "timeouts": (
        "Controls how long Terraform waits for provider operations. "
        "Field values are durations, such as \"5m\". "
        "This is not a property of the object itself, and most users "
        "do not need to set it."
    ),
}

# The four things a user may ask us to show for one resource / data source.
SHOW_CHOICES = ("all", "required", "optional", "attributes")

# The kinds of things a provider can contain.
# Note the spelling: humans say "datasource", Terraform JSON uses a longer key.
KIND_CHOICES = ("all", "resources", "datasources", "ephemerals", "functions")

# Map our short, friendly kind names to the keys used in Terraform's JSON.
# This is a dictionary: look up a friendly name, get the JSON key.
KIND_TO_JSON_KEY = {
    "resources": "resource_schemas",
    "datasources": "data_source_schemas",
    "ephemerals": "ephemeral_resource_schemas",
}

# Singular labels stored on each ProviderItem.kind.
KIND_TO_LABEL = {
    "resources": "resource",
    "datasources": "datasource",
    "ephemerals": "ephemeral",
}

# Reverse the map above: look up a JSON key, get our friendly singular label.
JSON_KEY_TO_LABEL = {
    json_key: KIND_TO_LABEL[kind]
    for kind, json_key in KIND_TO_JSON_KEY.items()
}


# ---------------------------------------------------------------------------
# Small "record" types for schema entries
# ---------------------------------------------------------------------------

@dataclass
class ProviderItem:
    """
    Everything we need to know about ONE resource, data source,
    ephemeral resource, or provider configuration block.

    A ``@dataclass`` is a class that mostly just holds data.
    Python writes the boring ``__init__`` method for us.

    Example of the data held here:
        name            = "aws_instance"
        kind            = "resource"   (or "datasource" / "ephemeral" / "provider")
        provider        = "aws"        (short provider name)
        block           = {...}        (the raw dictionary from the JSON)
        schema_version  = 0            (internal state-schema version, if any)
        description     = "..."        (human text, if the schema provides it)
        deprecated      = "..."        (message or True, if the item is obsolete)
    """

    name: str
    kind: str
    provider: str
    block: dict
    schema_version: int | None = None
    description: str | None = None
    deprecated: str | bool | None = None


@dataclass
class FunctionParameter:
    """One named parameter of a provider-defined function."""

    name: str
    type_value: object
    allow_null: bool
    description: str


@dataclass
class FunctionItem:
    """
    Everything we need to know about ONE provider-defined function.

    Provider functions are callable from Terraform expressions and are
    described under ``function_schemas`` in the provider JSON.
    """

    name: str
    provider: str
    summary: str
    description: str
    parameters: list[FunctionParameter]
    variadic_parameter: FunctionParameter | None
    return_type: object
    deprecated: str | bool | None = None


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
            "    python3 tfp.py --demo list\n"
            "    python3 tfp.py --self-test"
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


def extract_item_metadata(item_data: dict, block: dict) -> tuple:
    """
    Pull description, deprecation and schema version from an item entry.

    Prefers the block-level description / deprecated fields when present,
    then falls back to the outer item entry.  Returns
    ``(schema_version, description, deprecated)``.
    """
    schema_version = item_data.get("version")
    description = block.get("description") or item_data.get("description")
    if isinstance(description, str):
        description = " ".join(description.split()) or None
    else:
        description = None

    deprecated = block.get("deprecated")
    if deprecated is None:
        deprecated = item_data.get("deprecated")
    if isinstance(deprecated, str):
        deprecated = " ".join(deprecated.split()) or None

    return schema_version, description, deprecated


def collect_items(schema: dict, kind: str = "all") -> list[ProviderItem]:
    """
    Collect resources, data sources and/or ephemeral resources.

    Args:
        schema: The full dictionary returned by Terraform.
        kind:   "all", "resources", "datasources", or "ephemerals".

    Returns:
        A list of ProviderItem objects, sorted by kind, then by name.
        Sorting makes the output stable and easy to scan.

    Missing sections (for example ``ephemeral_resource_schemas`` on an
    older provider) are treated as empty, so the call still succeeds.
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
            label = JSON_KEY_TO_LABEL[json_key]
            # Missing keys are treated as empty, so older schemas work.
            schemas_for_kind = provider_data.get(json_key, {})

            for name, item_data in schemas_for_kind.items():
                # In the JSON, the useful part lives under the "block" key.
                block = item_data.get("block", {})
                schema_version, description, deprecated = extract_item_metadata(
                    item_data, block
                )

                items.append(
                    ProviderItem(
                        name=name,
                        kind=label,
                        provider=provider_name,
                        block=block,
                        schema_version=schema_version,
                        description=description,
                        deprecated=deprecated,
                    )
                )

    # Sort by kind first ("datasource" before "resource") and then by name.
    # A "lambda" is a tiny one-line function: here it builds the sort key.
    items.sort(key=lambda item: (item.kind, item.name))
    return items


def collect_provider_configs(schema: dict) -> list[ProviderItem]:
    """
    Collect each provider's own configuration block.

    These are the arguments you write inside ``provider "aws" { ... }``,
    not the resources or data sources the provider manages.
    """
    configs: list[ProviderItem] = []
    provider_schemas = schema.get("provider_schemas", {})

    for provider_address, provider_data in provider_schemas.items():
        provider_name = get_short_provider_name(provider_address)
        # The provider's own schema sits under the "provider" key.
        provider_section = provider_data.get("provider", {})
        block = provider_section.get("block", {})
        # Skip providers that declare no configuration arguments at all.
        if not block.get("attributes") and not block.get("block_types"):
            continue

        schema_version, description, deprecated = extract_item_metadata(
            provider_section, block
        )
        configs.append(
            ProviderItem(
                name=provider_name,
                kind="provider",
                provider=provider_name,
                block=block,
                schema_version=schema_version,
                description=description,
                deprecated=deprecated,
            )
        )

    configs.sort(key=lambda item: item.name)
    return configs


def parse_function_parameter(name: str, parameter_data: dict) -> FunctionParameter:
    """Turn one raw function-parameter dictionary into a FunctionParameter."""
    description = parameter_data.get("description") or ""
    if isinstance(description, str):
        description = " ".join(description.split())
    return FunctionParameter(
        name=name,
        type_value=parameter_data.get("type", "unknown"),
        allow_null=bool(parameter_data.get("allow_null_value", False)),
        description=description,
    )


def collect_functions(schema: dict) -> list[FunctionItem]:
    """
    Collect every provider-defined function from the schema.

    Missing ``function_schemas`` sections are treated as empty, so
    older Terraform / provider versions keep working unchanged.
    """
    functions: list[FunctionItem] = []
    provider_schemas = schema.get("provider_schemas", {})

    for provider_address, provider_data in provider_schemas.items():
        provider_name = get_short_provider_name(provider_address)
        function_schemas = provider_data.get("function_schemas", {})

        for name, function_data in function_schemas.items():
            parameters = [
                parse_function_parameter(param.get("name", f"arg{index}"), param)
                for index, param in enumerate(function_data.get("parameters", []))
            ]

            variadic = None
            variadic_data = function_data.get("variadic_parameter")
            if isinstance(variadic_data, dict):
                variadic = parse_function_parameter(
                    variadic_data.get("name", "args"),
                    variadic_data,
                )

            summary = " ".join(str(function_data.get("summary", "")).split())
            description = " ".join(
                str(function_data.get("description", "")).split()
            )
            deprecated = function_data.get("deprecated")
            if isinstance(deprecated, str):
                deprecated = " ".join(deprecated.split()) or None

            return_type = function_data.get("return_type", "unknown")
            # Some schemas nest the return type under {"type": ...}.
            if isinstance(return_type, dict):
                return_type = return_type.get("type", return_type)

            functions.append(
                FunctionItem(
                    name=name,
                    provider=provider_name,
                    summary=summary,
                    description=description,
                    parameters=parameters,
                    variadic_parameter=variadic,
                    return_type=return_type,
                    deprecated=deprecated,
                )
            )

    functions.sort(key=lambda item: (item.provider, item.name))
    return functions


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


def group_items_by_provider(
    items: list[ProviderItem],
) -> list[tuple[str, list[ProviderItem]]]:
    """
    Group items by provider.

    Returns a list of ``(provider, items)`` pairs, ordered by provider
    name.  Inside each group, items are ordered by kind, then by name.
    Returning ordered groups lets the printing code show each provider
    heading exactly once, with its items underneath it.
    """
    # First pass: put each item into a list for its provider.
    items_by_provider: dict[str, list[ProviderItem]] = {}
    for item in items:
        items_by_provider.setdefault(item.provider, []).append(item)

    # Second pass: sort the providers, and sort the items inside each one.
    grouped_items: list[tuple[str, list[ProviderItem]]] = []
    for provider in sorted(items_by_provider):
        provider_items = sorted(
            items_by_provider[provider],
            key=lambda item: (item.kind, item.name),
        )
        grouped_items.append((provider, provider_items))
    return grouped_items


def order_items_by_provider(items: list[ProviderItem]) -> list[ProviderItem]:
    """
    Return one flat list in the same order used by provider-grouped output.

    Menus must use this order: the number printed beside an item has to
    select that same item when the user types the number.
    """
    return [
        item
        for _provider, provider_items in group_items_by_provider(items)
        for item in provider_items
    ]


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


def is_sensitive(attribute: dict) -> bool:
    """Return True if this value is secret-like (for example a password)."""
    return bool(attribute.get("sensitive", False))


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


def build_attribute_description(attribute: dict) -> str:
    """
    Build the text for the DESCRIPTION column of one attribute.

    Sensitive is shown in the SENS column as an X, so it is not repeated
    here.  Deprecated notes stay in the description.
    """
    notes: list[str] = []

    # Collapse runs of whitespace (including newlines) into single
    # spaces, so the description fits neatly on wrapped table lines.
    description = " ".join(str(attribute.get("description", "")).split())
    if description:
        notes.append(description)

    deprecated = attribute.get("deprecated")
    if isinstance(deprecated, str) and deprecated:
        notes.append(f"Deprecated: {' '.join(deprecated.split())}")
    elif deprecated:
        # Sometimes the value is simply True instead of a message.
        notes.append("Deprecated.")

    return " ".join(notes)


def get_nested_type(attribute: dict) -> dict | None:
    """
    Return the nested_type dictionary for an attribute, or None.

    Modern provider-framework schemas can nest attributes inside another
    attribute using ``nested_type`` instead of (or as well as) a plain
    ``type`` string.  When present, that nested structure is expanded
    under the parent attribute in the output.
    """
    nested = attribute.get("nested_type")
    return nested if isinstance(nested, dict) else None


def format_nested_type_name(nested_type: dict) -> str:
    """
    Turn a nested_type's nesting mode into a short type label.

    Examples:
        {"nesting_mode": "single", ...} -> "object"
        {"nesting_mode": "list", ...}   -> "list of objects"
        {"nesting_mode": "set", ...}    -> "set of objects"
        {"nesting_mode": "map", ...}    -> "map of objects"
    """
    nesting_mode = nested_type.get("nesting_mode", "single")
    if nesting_mode == "single":
        return "object"
    if nesting_mode in {"list", "set", "map"}:
        return f"{nesting_mode} of objects"
    return f"{nesting_mode} of objects"


def format_attribute_type(attribute: dict) -> str:
    """
    Choose the readable type for one attribute.

    Prefers a plain ``type`` when present.  Falls back to the nested_type
    label when the attribute is a nested object / list / set / map of
    objects with no plain type of its own.
    """
    if "type" in attribute:
        return format_type(attribute.get("type", "unknown"))

    nested = get_nested_type(attribute)
    if nested is not None:
        return format_nested_type_name(nested)

    return "unknown"


def build_attribute_row(name: str, attribute: dict) -> tuple[str, ...]:
    """
    Build one row of the attribute table.

    The cells follow ATTRIBUTE_HEADERS, in the same order:
    Name, Type, REQ, OPT, COMP, SENS, Description.

    Flag columns contain FLAG_MARKER ("X") when the property is true,
    and are blank otherwise.  A value that is optional AND computed
    gets an X in both OPT and COMP.
    """
    return (
        name,
        format_attribute_type(attribute),
        FLAG_MARKER if is_required(attribute) else "",
        FLAG_MARKER if is_optional(attribute) else "",
        FLAG_MARKER if is_computed(attribute) else "",
        FLAG_MARKER if is_sensitive(attribute) else "",
        build_attribute_description(attribute),
    )


# ---------------------------------------------------------------------------
# Deciding whether nested blocks should be printed
# ---------------------------------------------------------------------------

def is_block_required(block_type: dict) -> bool:
    """
    Return True if at least one of this nested block must be provided.

    In the schema, that is ``min_items`` being 1 or more.
    """
    return block_type.get("min_items", 0) >= 1


def describe_block_cardinality(block_type: dict) -> str:
    """
    Translate raw schema cardinality into a plain-language sentence.

    The schema encodes how many of a block may appear as a nesting
    mode (single / list / set) plus ``min_items`` and ``max_items``.
    That is precise but unfriendly, e.g. "set, min: 0".  This turns it
    into wording such as "Optional block; may be repeated. Each block
    must be unique."
    """
    nesting_mode = block_type.get("nesting_mode", "single")
    min_items = block_type.get("min_items", 0)
    max_items = block_type.get("max_items")

    # A "set" cannot contain the same block twice.
    uniqueness = " Each block must be unique." if nesting_mode == "set" else ""

    if is_block_required(block_type):
        if max_items == 1:
            return "Required block; exactly one must be provided."
        if max_items is not None:
            return (
                f"Required block; at least {min_items} must be provided, "
                f"and no more than {max_items}."
            )
        if nesting_mode == "single":
            return "Required block; exactly one must be provided."
        return f"Required block; at least {min_items} must be provided."

    # From here on, the block itself is optional (min_items is 0).
    if nesting_mode == "single" or max_items == 1:
        return "Optional block; may appear at most once."
    if max_items is not None:
        return f"Optional block; may be repeated up to {max_items} times.{uniqueness}"
    return f"Optional block; may be repeated.{uniqueness}"


def classify_block(name: str, item_kind: str | None) -> str:
    """
    Classify a nested block by what it is FOR.

    Returns one of:
    * "lookup_filter" -- a data-source ``filter`` block
    * "timeouts"      -- a provider ``timeouts`` block
    * "configuration" -- everything else
    """
    if name == "filter" and item_kind == "datasource":
        return "lookup_filter"
    if name == "timeouts":
        return "timeouts"
    return "configuration"


def get_block_purpose(name: str, item_kind: str | None) -> str:
    """
    Return a plain-language purpose for a nested block.

    Known block names use BLOCK_PURPOSE_EXPLANATIONS.  The ``filter``
    explanation only applies to data sources, where filtering is a
    lookup mechanism; a ``filter`` block inside a resource keeps the
    neutral fallback, because there it configures the resource itself.
    """
    category = classify_block(name, item_kind)
    if category == "lookup_filter":
        return BLOCK_PURPOSE_EXPLANATIONS["filter"]
    if category == "timeouts":
        return BLOCK_PURPOSE_EXPLANATIONS["timeouts"]
    return "Provider-defined nested configuration block."


def build_block_example_lines(
    name: str,
    block_type: dict,
    item_kind: str | None,
) -> list[str]:
    """
    Build a tiny HCL example for block types that benefit from one.

    Only the special ``filter`` and ``timeouts`` blocks get an example;
    other blocks return an empty list (no example is printed).
    """
    category = classify_block(name, item_kind)

    if category == "lookup_filter":
        return [
            "filter {",
            '  name   = "tag:Name"',
            '  values = ["example"]',
            "}",
        ]

    if category == "timeouts":
        # Prefer showing "read" (the data-source operation); otherwise
        # show the first field the provider actually offers.
        fields = sorted(block_type.get("block", {}).get("attributes", {}))
        field = "read" if "read" in fields else (fields[0] if fields else "read")
        return [
            "timeouts {",
            f'  {field} = "5m"',
            "}",
        ]

    return []


def block_type_matches_show(block_type: dict, show: str) -> bool:
    """
    Decide whether one nested block should be printed for ``show``.

    A nested block is required when Terraform demands at least one of
    them.  In the JSON that is ``min_items`` being 1 or more.
    """
    if show == "all":
        return True
    if show == "required":
        return is_block_required(block_type)
    if show == "optional":
        return not is_block_required(block_type)
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

def get_column_widths(
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
    max_widths: tuple[int | None, ...] | None = None,
) -> list[int]:
    """
    Work out how wide each table column needs to be.

    A column is as wide as its widest cell (or its heading, if that is
    wider).  Columns named in ``max_widths`` are capped at that width;
    longer text in them is wrapped instead.  A None cap means "no cap".
    """
    widths: list[int] = []
    for column_index, header in enumerate(headers):
        # Start with the heading itself; every column is at least this wide.
        width = len(header)
        for row in rows:
            width = max(width, len(row[column_index]))

        # Apply the cap for this column, if one was given.
        if max_widths is not None:
            max_width = max_widths[column_index]
            if max_width is not None:
                width = min(width, max_width)

        widths.append(width)
    return widths


def wrap_cell_text(text: str, width: int) -> list[str]:
    """
    Wrap one table cell into lines that fit inside ``width`` characters.

    Returns a list with at least one line (an empty cell gives [""]),
    so callers can always line cells up side by side.
    """
    if not text:
        return [""]
    # break_long_words lets one very long word (e.g. a long type name)
    # be split instead of pushing the whole table sideways.
    wrapped = textwrap.wrap(
        text,
        width=max(width, 1),
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapped if wrapped else [""]


def print_table(
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
    indent: str = "",
    max_widths: tuple[int | None, ...] | None = None,
) -> None:
    """
    Print a simple aligned text table.

    Columns are separated by two spaces.  Cells that are too long for
    their column are wrapped: the extra text appears on the lines
    below, still inside the same column, with the other columns blank.
    """
    widths = get_column_widths(headers, rows, max_widths)

    def format_cells(cells: tuple[str, ...] | list[str]) -> str:
        # .ljust(width) pads a cell with spaces up to the column width.
        # .rstrip() removes trailing spaces from the end of the line.
        return indent + "  ".join(
            cell.ljust(width) for cell, width in zip(cells, widths)
        ).rstrip()

    # Heading row, e.g. NAME  TYPE  REQUIRED ...
    print(format_cells(headers))

    # A separator line of dashes, one run per column.
    print(format_cells(tuple("-" * width for width in widths)))

    for row in rows:
        # Wrap every cell, then print as many physical lines as the
        # tallest (most wrapped) cell needs.
        wrapped_cells = [
            wrap_cell_text(cell, width) for cell, width in zip(row, widths)
        ]
        line_count = max(len(cell_lines) for cell_lines in wrapped_cells)
        for line_index in range(line_count):
            line_cells = [
                cell_lines[line_index] if line_index < len(cell_lines) else ""
                for cell_lines in wrapped_cells
            ]
            print(format_cells(line_cells))


def print_provider_heading(provider: str) -> None:
    """
    Print the heading that starts one provider's section.

    This is printed once per provider, so individual rows and items do
    not need to repeat the provider name over and over.
    """
    print(f"\nProvider: {provider}")


def print_items_list(items: list[ProviderItem], title: str) -> list[ProviderItem]:
    """
    Print a numbered list of resources / data sources, grouped by provider.

    Each provider's name is printed once, as a heading above its items.
    Items appear in columns (number, kind, name) under that heading.

    Returns the items in the order they were displayed.  Numbering
    matters: after a search, the user can type a number to choose one
    of the matches, so the returned order must match the printed order.
    """
    print(title)
    print("-" * len(title))

    # Remember the displayed order, so callers can map a typed number
    # back to exactly the item that was printed beside that number.
    displayed_items = order_items_by_provider(items)

    if not displayed_items:
        print("  (nothing found)")
        return displayed_items

    number = 1
    for provider, provider_items in group_items_by_provider(items):
        print_provider_heading(provider)

        # Build this provider's rows.  The number keeps counting up
        # across providers, so every printed number is unique.
        rows: list[tuple[str, ...]] = []
        for item in provider_items:
            rows.append((str(number), item.kind, item.name))
            number += 1

        print_table(("NUMBER", "KIND", "NAME"), rows)

    # An f-string (f"...") places values inside text using {curly braces}.
    print(f"\nTotal: {len(displayed_items)}")
    return displayed_items


def print_attribute_table(attributes: dict, indent_level: int = 0) -> None:
    """
    Print attributes as an aligned table.

    Columns: NAME, TYPE, REQ, OPT, COMP, SENS, DESCRIPTION.
    Flag columns use an X marker.  NAME and TYPE grow to the widest
    entry in the table so every row lines up; only DESCRIPTION wraps.
    """
    indent = "  " * indent_level
    print(f"{indent}{FLAG_LEGEND}")

    rows = [
        build_attribute_row(name, attributes[name])
        for name in sorted(attributes)
    ]
    print_table(
        ATTRIBUTE_HEADERS,
        rows,
        indent=indent,
        max_widths=(
            None,  # NAME: grow to the widest entry.
            None,  # TYPE: grow to the widest entry.
            None,  # REQ
            None,  # OPT
            None,  # COMP
            None,  # SENS
            MAX_DESCRIPTION_WIDTH,
        ),
    )


def print_nested_attributes(
    attributes: dict,
    show: str,
    indent_level: int = 0,
    path_prefix: str = "",
) -> None:
    """
    Print nested_type fields under their parent attributes.

    For each visible attribute that has a ``nested_type``, print a
    sub-table of its inner attributes, then recurse for deeper nesting.
    The path (for example ``endpoint.host``) tells the reader where
    each nested field sits in the hierarchy.
    """
    indent = "  " * indent_level

    for name in sorted(attributes):
        attribute = attributes[name]
        nested = get_nested_type(attribute)
        if nested is None:
            continue

        full_path = f"{path_prefix}.{name}" if path_prefix else name
        mode_label = format_nested_type_name(nested)
        print()
        print(
            f'{indent}Nested attributes under "{full_path}" '
            f"({mode_label}):"
        )

        inner_attributes = nested.get("attributes", {})
        visible_inner = filter_attributes(inner_attributes, show)
        if visible_inner:
            print_attribute_table(visible_inner, indent_level)
            # Recurse so deeper nested_type levels are also shown.
            print_nested_attributes(
                visible_inner,
                show,
                indent_level=indent_level + 1,
                path_prefix=full_path,
            )
        else:
            print(f"{indent}  (no nested fields match --show {show})")


def print_one_block_type(
    name: str,
    block_type: dict,
    show: str,
    indent_level: int,
    item_kind: str | None,
) -> None:
    """
    Print ONE nested block: heading, explanation, example, and fields.

    The heading uses plain language (what the block is for, and whether
    it is optional / required / repeatable) instead of raw schema
    wording like "block (set, min: 0)".
    """
    indent = "  " * indent_level
    inner_indent = indent + "  "

    # Block name, then how many of it may appear.
    print(f"{inner_indent}{name}")
    print(f"{inner_indent}{describe_block_cardinality(block_type)}")

    # Purpose: the provider's own description (if any), plus our
    # plain-language explanation for known or unknown blocks.
    print(f"{inner_indent}Purpose:")
    schema_description = " ".join(
        str(block_type.get("description", "")).split()
    )
    purpose_lines: list[str] = []
    if schema_description:
        purpose_lines.append(schema_description)
    purpose = get_block_purpose(name, item_kind)
    if purpose not in purpose_lines:
        purpose_lines.append(purpose)
    for purpose_line in purpose_lines:
        # Wrap long explanations so they stay readable in a terminal.
        for wrapped_line in textwrap.wrap(purpose_line, width=76):
            print(f"{inner_indent}  {wrapped_line}")

    # A tiny HCL example, for the block types that benefit from one.
    example_lines = build_block_example_lines(name, block_type, item_kind)
    if example_lines:
        print(f"{inner_indent}Written as:")
        for example_line in example_lines:
            print(f"{inner_indent}  {example_line}")

    # Fields inside the block.  If the block itself is optional, its
    # "required" fields are only required once you choose to write the
    # block -- say so, instead of looking unconditional.
    if is_block_required(block_type):
        fields_heading = "Fields:"
    else:
        fields_heading = "Fields — required only if you write this block:"

    # Print the inside of this block by calling print_block() again,
    # one level deeper, with our fields heading.
    nested_block = block_type.get("block", {})
    print_block(
        nested_block,
        show,
        indent_level + 2,
        item_kind=item_kind,
        fields_heading=fields_heading,
    )


def print_block(
    block: dict,
    show: str,
    indent_level: int = 0,
    item_kind: str | None = None,
    fields_heading: str | None = None,
) -> None:
    """
    Print the attributes and nested blocks of ONE block, recursively.

    ``indent_level`` controls how far the text is pushed to the right.
    Deeper nesting = more indentation = easier to see the structure.

    ``item_kind`` ("resource" / "datasource") lets data-source lookup
    blocks be labelled correctly.  ``fields_heading`` replaces the
    default attributes heading when a parent block has already
    explained the context (e.g. "Fields — required only if ...").
    """
    # Two spaces per level.  " " * 4 means four spaces, and so on.
    indent = "  " * indent_level

    # --- Plain attributes at this level --------------------------------
    all_attributes = block.get("attributes", {})
    visible_attributes = filter_attributes(all_attributes, show)

    if visible_attributes:
        if fields_heading is not None:
            # The fields heading belongs to the parent block, so it is
            # printed one level above the table itself.
            heading_indent = "  " * max(indent_level - 1, 0)
            print(f"{heading_indent}{fields_heading}")
        elif item_kind == "provider" and indent_level == 0:
            print(f"{indent}Configuration arguments:")
        elif item_kind == "datasource" and indent_level == 0:
            # Data sources are looked up, then read.  Say which is which.
            print(f"{indent}Lookup arguments and returned attributes:")
            print(
                f"{indent}For a data source, OPTIONAL values can be used "
                "to find the object."
            )
            print(
                f"{indent}COMPUTED values are returned from the object "
                "that was found."
            )
        else:
            print(f"{indent}Arguments / attributes:")
        # The table sorts the names alphabetically, one attribute per row.
        print_attribute_table(visible_attributes, indent_level)
        # Expand any nested_type attributes underneath their parents.
        print_nested_attributes(
            visible_attributes,
            show,
            indent_level=indent_level,
        )
    elif indent_level == 0 and fields_heading is None:
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

    # Group blocks by purpose, so lookup filters and timeouts are not
    # mixed in with ordinary configuration.  Ordinary blocks come
    # first, then lookup filters, then provider operation timeouts.
    group_headings = (
        ("configuration", "Nested configuration blocks:"),
        ("lookup_filter", "Lookup filter blocks:"),
        ("timeouts", "Provider operation timeouts:"),
    )
    names_by_category: dict[str, list[str]] = {
        category: [] for category, _heading in group_headings
    }
    for name in sorted(visible_block_types):
        category = classify_block(name, item_kind)
        names_by_category[category].append(name)

    for category, heading in group_headings:
        block_names = names_by_category[category]
        if not block_names:
            continue
        print()
        print(f"{indent}{heading}")
        print(f"{indent}{'-' * len(heading)}")
        for name in block_names:
            print_one_block_type(
                name,
                visible_block_types[name],
                show,
                indent_level,
                item_kind,
            )


def print_item_metadata(item: ProviderItem) -> None:
    """
    Print description, schema version and deprecation for one item.

    Only lines that have a value in the schema are printed, so items
    without metadata stay quiet.
    """
    if item.description:
        for line in textwrap.wrap(
            f"Description: {item.description}",
            width=80,
        ):
            print(line)

    if item.schema_version is not None:
        print(f"Schema version: {item.schema_version}")

    if item.deprecated:
        if isinstance(item.deprecated, str):
            deprecated_text = item.deprecated
        else:
            deprecated_text = "This item is deprecated."
        for line in textwrap.wrap(
            f"Deprecated: {deprecated_text}",
            width=80,
        ):
            print(line)

    if item.kind == "ephemeral":
        print(
            "Note: Values are available only during the Terraform "
            "operation and are not saved in state or plan files."
        )


def print_item_details(item: ProviderItem, show: str) -> None:
    """
    Print a clear heading, then the chosen details for one item.

    The provider is deliberately NOT printed here: callers print it
    once, as a section heading above one or more items.
    """
    # A line of "=" characters, as long as the heading text.
    if item.kind == "provider":
        heading = f"Provider configuration  [provider]"
    else:
        heading = f"{item.name}  [{item.kind}]"
    print()
    print(heading)
    print("=" * len(heading))
    print_item_metadata(item)
    print(f"Showing: {show}")
    print()

    # Pass the kind along so nested blocks can be labelled correctly
    # (for example, a data-source "filter" block is a lookup filter).
    print_block(item.block, show, item_kind=item.kind)


def print_function_details(item: FunctionItem) -> None:
    """Print the summary, parameters and return type of one function."""
    heading = f"{item.name}  [function]"
    print()
    print(heading)
    print("=" * len(heading))

    if item.summary:
        for line in textwrap.wrap(f"Summary: {item.summary}", width=80):
            print(line)
    if item.description and item.description != item.summary:
        for line in textwrap.wrap(
            f"Description: {item.description}",
            width=80,
        ):
            print(line)
    if item.deprecated:
        if isinstance(item.deprecated, str):
            deprecated_text = item.deprecated
        else:
            deprecated_text = "This function is deprecated."
        for line in textwrap.wrap(
            f"Deprecated: {deprecated_text}",
            width=80,
        ):
            print(line)

    print(f"Returns: {format_type(item.return_type)}")
    print()

    if item.parameters:
        print("Parameters:")
        rows = [
            (
                parameter.name,
                format_type(parameter.type_value),
                "yes" if parameter.allow_null else "no",
                parameter.description,
            )
            for parameter in item.parameters
        ]
        print_table(
            ("PARAMETER", "TYPE", "ALLOW NULL", "DESCRIPTION"),
            rows,
            max_widths=(None, None, None, MAX_DESCRIPTION_WIDTH),
        )
    else:
        print("Parameters: (none)")

    if item.variadic_parameter is not None:
        print()
        print("Variadic parameter:")
        variadic = item.variadic_parameter
        print_table(
            ("PARAMETER", "TYPE", "ALLOW NULL", "DESCRIPTION"),
            [
                (
                    variadic.name,
                    format_type(variadic.type_value),
                    "yes" if variadic.allow_null else "no",
                    variadic.description,
                )
            ],
            max_widths=(None, None, None, MAX_DESCRIPTION_WIDTH),
        )


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

def handle_list(
    items: list[ProviderItem],
    functions: list[FunctionItem],
    kind: str,
) -> int:
    """Handle:  list [--kind all|resources|datasources|ephemerals|functions]"""
    # Functions are listed separately so the numbered list stays one kind
    # of entry.  Everything else is a ProviderItem with a shared shape.
    if kind == "functions":
        return handle_list_functions(functions)

    # `kind` arrives as a plural word; each item stores a singular label.
    if kind == "resources":
        visible = [item for item in items if item.kind == "resource"]
    elif kind == "datasources":
        visible = [item for item in items if item.kind == "datasource"]
    elif kind == "ephemerals":
        visible = [item for item in items if item.kind == "ephemeral"]
    else:  # "all" -- resources, data sources and ephemerals together.
        visible = items

    print_items_list(visible, f"Listing: {kind}")
    return 0  # 0 means "the program succeeded" (a Unix convention).


def handle_list_functions(functions: list[FunctionItem]) -> int:
    """List every provider-defined function, grouped by provider."""
    print("Listing: functions")
    print("------------------")

    if not functions:
        print("  (nothing found)")
        return 0

    # Group by provider, then print a numbered KIND / NAME table.
    by_provider: dict[str, list[FunctionItem]] = {}
    for function in functions:
        by_provider.setdefault(function.provider, []).append(function)

    number = 1
    for provider in sorted(by_provider):
        print_provider_heading(provider)
        rows: list[tuple[str, ...]] = []
        for function in by_provider[provider]:
            rows.append((str(number), "function", function.name))
            number += 1
        print_table(("NUMBER", "KIND", "NAME"), rows)

    print(f"\nTotal: {len(functions)}")
    return 0


def handle_search(
    items: list[ProviderItem],
    functions: list[FunctionItem],
    keyword: str,
    show: str | None,
) -> int:
    """
    Handle:  search KEYWORD [--show all|required|optional|attributes]

    Without ``--show``, this only lists the matches.
    With ``--show``, the user picks a match and we display its details.
    """
    matches = search_items(items, keyword)
    function_matches = search_functions(functions, keyword)

    if not matches and not function_matches:
        print(f"No resources, data sources, ephemerals or functions match: {keyword!r}")
        return 0

    # Prefer ProviderItem matches for the interactive chooser; function
    # matches are listed underneath as a separate section.
    displayed_matches: list[ProviderItem] = []
    if matches:
        displayed_matches = print_items_list(
            matches,
            f"Search results for: {keyword!r}",
        )
    if function_matches:
        print()
        handle_list_functions(function_matches)

    # If the user did not ask to view details, our work is done.
    if show is None:
        if displayed_matches:
            print(
                "\nTip: add --show required (for example) to view one of these."
            )
        return 0

    if not displayed_matches:
        print(
            "\nTip: use 'show NAME --kind function' to view a matching function."
        )
        return 0

    chosen = choose_item_from_list(displayed_matches)
    if chosen is None:
        return 0

    # The provider heading was already printed above, in the results
    # list, so the details do not repeat it.
    print_item_details(chosen, show)
    return 0


def search_functions(
    functions: list[FunctionItem],
    keyword: str,
) -> list[FunctionItem]:
    """Return every function whose name contains ``keyword`` (case-insensitive)."""
    folded_keyword = keyword.casefold()
    return [
        function
        for function in functions
        if folded_keyword in function.name.casefold()
    ]


def find_exact_functions(
    functions: list[FunctionItem],
    name: str,
) -> list[FunctionItem]:
    """Return functions whose name matches ``name`` exactly (ignoring case)."""
    folded_name = name.casefold()
    return [
        function
        for function in functions
        if function.name.casefold() == folded_name
    ]


def handle_show(
    items: list[ProviderItem],
    functions: list[FunctionItem],
    name: str,
    kind: str | None,
    show: str,
) -> int:
    """
    Handle:  show NAME [--kind resource|datasource|ephemeral|function] [--show ...]

    ``NAME`` must match exactly, e.g. ``aws_instance``.
    If nothing matches exactly, we fall back to a keyword search so the
    user still gets helpful suggestions instead of a dead end.
    """
    if kind == "function":
        return handle_show_function(functions, name)

    exact_matches = filter_items_by_kind(find_exact_items(items, name), kind)

    # When no --kind was given, also try exact function matches.
    if not exact_matches and kind is None:
        function_matches = find_exact_functions(functions, name)
        if function_matches:
            return handle_show_function(functions, name)

    if exact_matches:
        # The same name can exist as a resource AND a data source, and
        # even in more than one provider.  Group by provider so each
        # provider heading is printed once, above its own items.
        for provider, provider_items in group_items_by_provider(exact_matches):
            print_provider_heading(provider)
            for item in provider_items:
                print_item_details(item, show)
        return 0

    # No exact match: be helpful and offer the closest keyword matches.
    print(f"No exact resource, data source, ephemeral or function named: {name!r}")
    suggestions = filter_items_by_kind(search_items(items, name), kind)
    function_suggestions = (
        search_functions(functions, name) if kind in (None, "function") else []
    )

    if not suggestions and not function_suggestions:
        print("No similar names were found either. Try: list")
        return 0

    if suggestions:
        print("\nDid you mean one of these?")
        displayed_suggestions = print_items_list(suggestions, "Similar names")
        chosen = choose_item_from_list(displayed_suggestions)
        if chosen is not None:
            print_item_details(chosen, show)
    if function_suggestions:
        print("\nMatching functions:")
        handle_list_functions(function_suggestions)
    return 0


def handle_show_function(functions: list[FunctionItem], name: str) -> int:
    """Show one exact provider-defined function."""
    matches = find_exact_functions(functions, name)
    if not matches:
        print(f"No exact function named: {name!r}")
        suggestions = search_functions(functions, name)
        if suggestions:
            print("\nDid you mean one of these?")
            handle_list_functions(suggestions)
        return 0

    # Group by provider so each heading is printed once.
    by_provider: dict[str, list[FunctionItem]] = {}
    for function in matches:
        by_provider.setdefault(function.provider, []).append(function)

    for provider in sorted(by_provider):
        print_provider_heading(provider)
        for function in by_provider[provider]:
            print_function_details(function)
    return 0


def handle_provider(
    schema: dict,
    name: str,
    show: str,
) -> int:
    """
    Handle:  provider NAME [--show all|required|optional|attributes]

    Shows the configuration arguments of ``provider "NAME" { ... }``.
    """
    configs = collect_provider_configs(schema)
    folded_name = name.casefold()
    matches = [
        config for config in configs if config.name.casefold() == folded_name
    ]

    if not matches:
        available = ", ".join(config.name for config in configs) or "(none)"
        print(f"No provider configuration named: {name!r}")
        print(f"Available providers with configuration: {available}")
        return 0

    for config in matches:
        print_provider_heading(config.provider)
        print_item_details(config, show)
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
                    "ephemeral resources, functions, arguments and attributes.",
        epilog=(
            "Examples:\n"
            "  %(prog)s list\n"
            "  %(prog)s list --kind datasources\n"
            "  %(prog)s list --kind functions\n"
            "  %(prog)s list --kind ephemerals\n"
            "  %(prog)s search ec2\n"
            "  %(prog)s search bucket --show optional\n"
            "  %(prog)s show aws_instance --show required\n"
            "  %(prog)s show aws_ami --kind datasource --show attributes\n"
            "  %(prog)s show cidr_contains --kind function\n"
            "  %(prog)s provider aws --show all\n"
            "  %(prog)s --demo show fake_server --show all\n"
            "  %(prog)s --self-test\n"
        ),
        # RawDescriptionHelpFormatter keeps our line breaks in the help text.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global options.  These go BEFORE the command word, e.g.
    #   python3 tfp.py --demo list
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
        help="List resources, data sources, ephemerals and/or functions.",
    )
    list_parser.add_argument(
        "--kind",
        choices=KIND_CHOICES,
        default="all",
        help="What to list (default: all = resources, datasources, ephemerals).",
    )

    # --- Command: search ------------------------------------------------
    search_parser = subparsers.add_parser(
        "search",
        help="Find items whose name contains a keyword.",
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
        choices=("resource", "datasource", "ephemeral", "function"),
        default=None,
        help="Only look at one kind of item.",
    )
    show_parser.add_argument(
        "--show",
        choices=SHOW_CHOICES,
        default="all",
        help="What to show (default: all).",
    )

    # --- Command: provider ----------------------------------------------
    provider_parser = subparsers.add_parser(
        "provider",
        help="Show a provider's own configuration arguments "
             '(e.g. provider "aws" { ... }).',
    )
    provider_parser.add_argument(
        "name",
        help="Short provider name, e.g. aws or fake.",
    )
    provider_parser.add_argument(
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

    # Turn the raw JSON into tidy lists once.  Every command then works
    # with the same lists, instead of re-reading the JSON each time.
    items = collect_items(schema)
    functions = collect_functions(schema)

    # The provider command only needs the schema; everything else needs
    # at least one item or function.
    if args.command == "provider":
        return handle_provider(schema, name=args.name, show=args.show)

    if not items and not functions:
        print(
            "No resources, data sources, ephemerals or functions were "
            "found in this schema.\n"
            "Tips: check that you are in a Terraform project folder, "
            "that the provider is used by the code there, and that you "
            "have run 'terraform init'."
        )
        return 0

    # Send the work to the one handler that matches the command.
    # Each handler returns its own exit code, which we pass straight on.
    if args.command == "list":
        return handle_list(items, functions, kind=args.kind)

    if args.command == "search":
        return handle_search(
            items,
            functions,
            keyword=args.keyword,
            show=args.show,
        )

    if args.command == "show":
        return handle_show(
            items,
            functions,
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
#     python3 tfp.py --self-test
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

    * a provider configuration block (including a sensitive argument),
    * a resource with required, optional and computed values,
    * a nested_type attribute with inner fields,
    * a nested block with its own required value inside it,
    * a second resource with description / deprecation metadata,
    * a data source with filter and timeouts blocks,
    * a provider-defined function,
    * and an ephemeral resource.
    """
    return {
        "format_version": "1.0",
        "provider_schemas": {
            "registry.terraform.io/hashicorp/fake": {
                "provider": {
                    "version": 0,
                    "block": {
                        "attributes": {
                            "access_key": {
                                "type": "string",
                                "optional": True,
                                "sensitive": True,
                                "description": "Static access key.",
                            },
                            "profile": {
                                "type": "string",
                                "optional": True,
                                "description": "Shared config profile.",
                            },
                            "region": {
                                "type": "string",
                                "optional": True,
                                "description": "AWS region to use.",
                            },
                            "shared_config_files": {
                                "type": ["list", "string"],
                                "optional": True,
                                "description": "Paths to shared config files.",
                            },
                        },
                        "block_types": {
                            "assume_role": {
                                "nesting_mode": "single",
                                "block": {
                                    "attributes": {
                                        "role_arn": {
                                            "type": "string",
                                            "optional": True,
                                        },
                                    }
                                },
                            }
                        },
                    },
                },
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
                                # Nested attributes (provider-framework style).
                                "endpoint": {
                                    "optional": True,
                                    "computed": True,
                                    "description": "Endpoint settings.",
                                    "nested_type": {
                                        "nesting_mode": "single",
                                        "attributes": {
                                            "host": {
                                                "type": "string",
                                                "optional": True,
                                                "computed": True,
                                                "description": "Host name.",
                                            },
                                            "port": {
                                                "type": "number",
                                                "optional": True,
                                                "computed": True,
                                                "description": "Port number.",
                                            },
                                            "secure": {
                                                "type": "bool",
                                                "optional": True,
                                                "description": "Use TLS.",
                                            },
                                        },
                                    },
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
                        "version": 1,
                        "block": {
                            "description": "A simple storage bucket.",
                            "deprecated": "Use fake_object_store instead.",
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
                            },
                            "block_types": {
                                # Optional, repeatable lookup filters,
                                # as on many real data sources.
                                "filter": {
                                    "nesting_mode": "set",
                                    "block": {
                                        "attributes": {
                                            "name": {
                                                "type": "string",
                                                "required": True,
                                            },
                                            "values": {
                                                "type": ["set", "string"],
                                                "required": True,
                                            },
                                        }
                                    },
                                },
                                # Optional provider operation timeouts.
                                "timeouts": {
                                    "nesting_mode": "single",
                                    "block": {
                                        "attributes": {
                                            "read": {
                                                "type": "string",
                                                "optional": True,
                                            },
                                        }
                                    },
                                },
                            },
                        },
                    }
                },
                "ephemeral_resource_schemas": {
                    "temporary_password": {
                        "version": 0,
                        "block": {
                            "description": "A short-lived password.",
                            "attributes": {
                                "length": {
                                    "type": "number",
                                    "optional": True,
                                },
                                "result": {
                                    "type": "string",
                                    "computed": True,
                                    "sensitive": True,
                                },
                            },
                        },
                    }
                },
                "function_schemas": {
                    "cidr_contains": {
                        "summary": "Checks whether an address belongs to a network.",
                        "description": (
                            "Returns true when the address is inside the "
                            "given CIDR network."
                        ),
                        "return_type": "bool",
                        "parameters": [
                            {
                                "name": "network",
                                "type": "string",
                                "description": "Network in CIDR notation.",
                            },
                            {
                                "name": "address",
                                "type": "string",
                                "description": "Address to test.",
                            },
                        ],
                    }
                },
            }
        },
    }


def make_multi_provider_schema() -> dict:
    """
    Build a fake schema with TWO providers, for grouping tests.

    Both providers contain a ``shared_thing`` resource, so tests can
    check that a name found in two providers gets one provider heading
    per provider -- not one heading per item.
    """

    def simple_block() -> dict:
        # A tiny block with one computed ``id`` is enough for grouping tests.
        return {"attributes": {"id": {"type": "string", "computed": True}}}

    return {
        "format_version": "1.0",
        "provider_schemas": {
            "registry.terraform.io/example/alpha": {
                "resource_schemas": {
                    "alpha_server": {"block": simple_block()},
                    "shared_thing": {"block": simple_block()},
                },
                "data_source_schemas": {
                    "alpha_image": {"block": simple_block()},
                },
            },
            "registry.terraform.io/example/beta": {
                "resource_schemas": {
                    "beta_server": {"block": simple_block()},
                    "shared_thing": {"block": simple_block()},
                },
                "data_source_schemas": {
                    "beta_image": {"block": simple_block()},
                },
            },
        },
    }


def capture_output(function, *args, **kwargs):
    """
    Run a printing function and capture everything it prints.

    Returns ``(result, printed_text)``.  This lets tests check the exact
    text a user would see, without printing it during the test run.
    """
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        result = function(*args, **kwargs)
    return result, stream.getvalue()


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
        # Our fake schema has 2 resources, 1 data source and 1 ephemeral.
        self.assertEqual(len(self.items), 4)
        kinds = {item.kind for item in self.items}
        self.assertEqual(kinds, {"resource", "datasource", "ephemeral"})

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
        self.assertEqual(sorted(result), ["endpoint", "size", "tags"])

    def test_attributes_keeps_only_computed(self):
        # Note: "tags" and "endpoint" appear here too, because they are
        # optional AND computed.  That overlap is real Terraform behaviour.
        result = filter_attributes(self.attributes, "attributes")
        self.assertEqual(
            sorted(result),
            ["endpoint", "id", "ip_address", "tags"],
        )

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


class TestGrouping(unittest.TestCase):
    """Tests for group_items_by_provider() and order_items_by_provider()."""

    def setUp(self):
        self.items = collect_items(make_multi_provider_schema())

    def test_groups_are_ordered_by_provider(self):
        groups = group_items_by_provider(self.items)
        self.assertEqual(
            [provider for provider, _items in groups],
            ["alpha", "beta"],
        )

    def test_items_inside_a_group_are_ordered_by_kind_then_name(self):
        groups = dict(group_items_by_provider(self.items))
        self.assertEqual(
            [item.name for item in groups["alpha"]],
            ["alpha_image", "alpha_server", "shared_thing"],
        )

    def test_flat_order_matches_grouped_display_order(self):
        ordered = order_items_by_provider(self.items)
        self.assertEqual(
            [(item.provider, item.name) for item in ordered],
            [
                ("alpha", "alpha_image"),
                ("alpha", "alpha_server"),
                ("alpha", "shared_thing"),
                ("beta", "beta_image"),
                ("beta", "beta_server"),
                ("beta", "shared_thing"),
            ],
        )


class TestPrintedOutput(unittest.TestCase):
    """Tests that capture printed output and check its layout."""

    def test_list_prints_each_provider_heading_once(self):
        items = collect_items(make_multi_provider_schema())
        displayed, output = capture_output(print_items_list, items, "Listing: all")

        # One heading per provider, and no per-row provider text.
        self.assertEqual(output.count("Provider: alpha"), 1)
        self.assertEqual(output.count("Provider: beta"), 1)
        self.assertNotIn("(provider:", output)

        # Items appear under the correct provider heading.
        self.assertLess(output.index("Provider: alpha"), output.index("alpha_server"))
        self.assertLess(output.index("alpha_server"), output.index("Provider: beta"))
        self.assertLess(output.index("Provider: beta"), output.index("beta_server"))

        # The list is printed in columns.
        self.assertIn("NUMBER", output)
        self.assertIn("KIND", output)
        self.assertIn("NAME", output)
        self.assertEqual(len(displayed), 6)

    def test_printed_numbers_select_the_same_items(self):
        items = collect_items(make_multi_provider_schema())
        displayed, output = capture_output(print_items_list, items, "Listing: all")

        # Read the printed rows back: number, kind, name.
        printed_rows = {}
        for line in output.splitlines():
            parts = line.split()
            if parts and parts[0].isdigit():
                printed_rows[int(parts[0])] = parts[2]

        # Choosing printed number N must select displayed_items[N - 1].
        for number, name in printed_rows.items():
            self.assertEqual(displayed[number - 1].name, name)

    def test_show_prints_provider_once_for_a_single_provider(self):
        items = collect_items(make_fake_schema())
        functions = collect_functions(make_fake_schema())
        _result, output = capture_output(
            handle_show, items, functions, "fake_server", None, "all"
        )
        self.assertEqual(output.count("Provider: fake"), 1)
        self.assertNotIn("(provider:", output)

    def test_show_groups_same_name_from_two_providers(self):
        items = collect_items(make_multi_provider_schema())
        _result, output = capture_output(
            handle_show, items, [], "shared_thing", None, "all"
        )

        # One heading per provider; the shared name appears once per provider.
        self.assertEqual(output.count("Provider: alpha"), 1)
        self.assertEqual(output.count("Provider: beta"), 1)
        self.assertEqual(output.count("shared_thing  [resource]"), 2)

    def test_attribute_columns_put_x_markers_in_the_right_columns(self):
        items = collect_items(make_fake_schema())
        server = find_exact_items(items, "fake_server")[0]
        _result, output = capture_output(print_item_details, server, "all")
        lines = output.splitlines()

        # Find the (first) column heading line and note where each
        # flag column starts.  Flag columns use short headings and X.
        header = next(line for line in lines if line.startswith("NAME"))
        req_at = header.index("REQ")
        opt_at = header.index("OPT")
        comp_at = header.index("COMP")
        sens_at = header.index("SENS")
        self.assertIn("DESCRIPTION", header)
        self.assertIn(FLAG_LEGEND, output)

        def row_starting_with(name: str) -> str:
            return next(
                line for line in lines if line.startswith(f"{name} ")
            )

        # Each X starts exactly under its own short heading.
        name_row = row_starting_with("name")
        self.assertEqual(name_row[req_at], FLAG_MARKER)

        # "tags" is optional AND computed: an X in both OPT and COMP.
        tags_row = row_starting_with("tags")
        self.assertEqual(tags_row[opt_at], FLAG_MARKER)
        self.assertEqual(tags_row[comp_at], FLAG_MARKER)

        id_row = row_starting_with("id")
        self.assertEqual(id_row[comp_at], FLAG_MARKER)

        # Whole words must not appear as flag cells.
        self.assertNotIn(" required", name_row)
        self.assertNotIn(" optional", tags_row)
        self.assertNotIn(" computed", id_row)

        # SENS is present on the heading even when empty for these rows.
        self.assertEqual(len(name_row) > sens_at, True)

    def test_long_description_wraps_inside_the_description_column(self):
        long_description = (
            "This is a deliberately long description that cannot fit "
            "inside one description column, so it must wrap neatly."
        )
        item = ProviderItem(
            name="long_server",
            kind="resource",
            provider="fake",
            block={
                "attributes": {
                    "setting": {
                        "type": "string",
                        "optional": True,
                        "description": long_description,
                    }
                }
            },
        )
        _result, output = capture_output(print_item_details, item, "all")
        lines = output.splitlines()
        header = next(line for line in lines if line.startswith("NAME"))
        description_at = header.index("DESCRIPTION")

        # The first row line carries the start of the description...
        row_index = next(
            index for index, line in enumerate(lines) if line.startswith("setting ")
        )
        self.assertIn("deliberately", lines[row_index])

        # ...and a continuation line follows, with text only in the
        # description column (everything before it is blank).
        continuation = lines[row_index + 1]
        self.assertTrue(continuation[description_at:].strip())
        self.assertEqual(continuation[:description_at].strip(), "")

    def test_nested_block_attributes_use_an_indented_table(self):
        items = collect_items(make_fake_schema())
        server = find_exact_items(items, "fake_server")[0]
        _result, output = capture_output(print_item_details, server, "all")
        lines = output.splitlines()

        # The nested "disk" block contains "gb": its row is indented,
        # and an indented column heading appears before it.
        gb_line = next(line for line in lines if line.strip().startswith("gb "))
        self.assertTrue(gb_line.startswith("    "))

        header_lines = [
            line for line in lines if line.strip().startswith("NAME")
        ]
        # Top-level attributes, nested_type "endpoint", and "disk" block.
        self.assertGreaterEqual(len(header_lines), 3)
        self.assertTrue(any(line.startswith("    ") for line in header_lines))


class TestNestedBlocks(unittest.TestCase):
    """Tests for plain-language nested block explanations."""

    def test_cardinality_translations(self):
        cases = [
            (
                {"nesting_mode": "single"},
                "Optional block; may appear at most once.",
            ),
            (
                {"nesting_mode": "set", "min_items": 0},
                "Optional block; may be repeated. Each block must be unique.",
            ),
            (
                {"nesting_mode": "list", "min_items": 0, "max_items": 3},
                "Optional block; may be repeated up to 3 times.",
            ),
            (
                {"nesting_mode": "list", "min_items": 1},
                "Required block; at least 1 must be provided.",
            ),
            (
                {"nesting_mode": "single", "min_items": 1, "max_items": 1},
                "Required block; exactly one must be provided.",
            ),
            (
                {"nesting_mode": "list", "min_items": 2, "max_items": 4},
                "Required block; at least 2 must be provided, and no more than 4.",
            ),
        ]
        for block_type, expected in cases:
            with self.subTest(block_type=block_type):
                self.assertEqual(describe_block_cardinality(block_type), expected)

    def test_is_block_required(self):
        self.assertTrue(is_block_required({"min_items": 1}))
        self.assertFalse(is_block_required({"min_items": 0}))
        self.assertFalse(is_block_required({}))

    def test_classify_block_depends_on_item_kind(self):
        # "filter" is a lookup block on data sources only.
        self.assertEqual(classify_block("filter", "datasource"), "lookup_filter")
        self.assertEqual(classify_block("filter", "resource"), "configuration")
        self.assertEqual(classify_block("timeouts", "resource"), "timeouts")
        self.assertEqual(classify_block("disk", "resource"), "configuration")

    def test_purpose_explanations(self):
        self.assertIn("Narrows the data-source lookup", get_block_purpose("filter", "datasource"))
        self.assertIn("provider operations", get_block_purpose("timeouts", "datasource"))
        self.assertEqual(
            get_block_purpose("disk", "resource"),
            "Provider-defined nested configuration block.",
        )

    def test_example_lines(self):
        filter_block = {
            "block": {"attributes": {"name": {}, "values": {}}}
        }
        self.assertIn('  name   = "tag:Name"', build_block_example_lines("filter", filter_block, "datasource"))
        self.assertEqual(build_block_example_lines("disk", filter_block, "resource"), [])

        timeouts_block = {"block": {"attributes": {"read": {}}}}
        self.assertIn('read = "5m"', build_block_example_lines("timeouts", timeouts_block, "datasource")[1])

    def test_datasource_output_explains_filter_and_timeouts(self):
        items = collect_items(make_fake_schema())
        functions = collect_functions(make_fake_schema())
        _result, output = capture_output(
            handle_show, items, functions, "fake_image", "datasource", "all"
        )

        # Grouped, explained sections -- not raw schema wording.
        self.assertIn("Lookup filter blocks:", output)
        self.assertIn("Provider operation timeouts:", output)
        self.assertIn("Optional block; may be repeated. Each block must be unique.", output)
        self.assertIn("Optional block; may appear at most once.", output)
        self.assertIn("Narrows the data-source lookup", output)
        self.assertIn("Fields — required only if you write this block:", output)
        self.assertIn("Written as:", output)
        self.assertIn('name   = "tag:Name"', output)
        self.assertIn('read = "5m"', output)
        self.assertNotIn("block (set", output)
        self.assertNotIn("block (single", output)

        # Lookup filters are shown before provider operation timeouts.
        self.assertLess(output.index("Lookup filter blocks:"), output.index("Provider operation timeouts:"))

    def test_unknown_block_uses_neutral_explanation(self):
        items = collect_items(make_fake_schema())
        server = find_exact_items(items, "fake_server")[0]
        _result, output = capture_output(print_item_details, server, "all")

        self.assertIn("Nested configuration blocks:", output)
        self.assertIn("Required block; at least 1 must be provided.", output)
        self.assertIn("Provider-defined nested configuration block.", output)
        self.assertNotIn("block (list", output)


class TestExpandedFeatures(unittest.TestCase):
    """Tests for provider config, metadata, nested_type, functions, ephemerals."""

    def setUp(self):
        self.schema = make_fake_schema()
        self.items = collect_items(self.schema)
        self.functions = collect_functions(self.schema)

    def test_provider_config_is_collected_and_rendered(self):
        configs = collect_provider_configs(self.schema)
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].kind, "provider")
        self.assertIn("access_key", configs[0].block["attributes"])

        _result, output = capture_output(
            handle_provider, self.schema, "fake", "all"
        )
        self.assertIn("Provider configuration  [provider]", output)
        self.assertIn("Configuration arguments:", output)
        self.assertEqual(output.count("Provider: fake"), 1)
        # Sensitive argument shows an X under SENS.
        header = next(
            line for line in output.splitlines() if line.startswith("NAME")
        )
        sens_at = header.index("SENS")
        access_row = next(
            line for line in output.splitlines()
            if line.startswith("access_key ")
        )
        self.assertEqual(access_row[sens_at], FLAG_MARKER)

    def test_columns_align_with_short_and_long_names(self):
        # The provider config mixes short names (region) with a long
        # name (shared_config_files) and a long type (list of string).
        _result, output = capture_output(
            handle_provider, self.schema, "fake", "all"
        )
        lines = output.splitlines()
        header = next(line for line in lines if line.startswith("NAME"))
        type_at = header.index("TYPE")
        opt_at = header.index("OPT")
        desc_at = header.index("DESCRIPTION")

        region = next(line for line in lines if line.startswith("region "))
        shared = next(
            line for line in lines if line.startswith("shared_config_files ")
        )

        # TYPE, OPT and DESCRIPTION start at identical positions.
        self.assertEqual(region.index("string"), type_at)
        self.assertEqual(shared.index("list of string"), type_at)
        self.assertEqual(region[opt_at], FLAG_MARKER)
        self.assertEqual(shared[opt_at], FLAG_MARKER)
        self.assertTrue(region[desc_at:].startswith("AWS region"))
        self.assertTrue(shared[desc_at:].startswith("Paths to shared"))

    def test_item_metadata_is_printed_when_present(self):
        bucket = find_exact_items(self.items, "fake_bucket")[0]
        self.assertEqual(bucket.schema_version, 1)
        self.assertEqual(bucket.description, "A simple storage bucket.")
        self.assertEqual(bucket.deprecated, "Use fake_object_store instead.")

        _result, output = capture_output(print_item_details, bucket, "all")
        self.assertIn("Description: A simple storage bucket.", output)
        self.assertIn("Schema version: 1", output)
        self.assertIn("Deprecated: Use fake_object_store instead.", output)

    def test_nested_type_attributes_are_expanded(self):
        server = find_exact_items(self.items, "fake_server")[0]
        _result, output = capture_output(print_item_details, server, "all")
        self.assertIn('Nested attributes under "endpoint" (object):', output)
        self.assertIn("host", output)
        self.assertIn("port", output)
        self.assertIn("object", output)
        # Parent row uses the nested type label, not "unknown".
        endpoint_row = next(
            line for line in output.splitlines()
            if line.startswith("endpoint ")
        )
        self.assertIn("object", endpoint_row)

    def test_functions_are_collected_and_rendered(self):
        self.assertEqual(len(self.functions), 1)
        self.assertEqual(self.functions[0].name, "cidr_contains")
        self.assertEqual(len(self.functions[0].parameters), 2)

        _result, output = capture_output(
            handle_show_function, self.functions, "cidr_contains"
        )
        self.assertIn("cidr_contains  [function]", output)
        self.assertIn("Returns: bool", output)
        self.assertIn("Parameters:", output)
        self.assertIn("network", output)
        self.assertIn("address", output)

    def test_ephemeral_resources_are_listed_and_noted(self):
        ephemerals = [item for item in self.items if item.kind == "ephemeral"]
        self.assertEqual([item.name for item in ephemerals], ["temporary_password"])

        _result, output = capture_output(
            print_item_details, ephemerals[0], "all"
        )
        self.assertIn("temporary_password  [ephemeral]", output)
        self.assertIn("not saved in state or plan files", output)

    def test_format_nested_type_name_modes(self):
        self.assertEqual(
            format_nested_type_name({"nesting_mode": "single"}),
            "object",
        )
        self.assertEqual(
            format_nested_type_name({"nesting_mode": "list"}),
            "list of objects",
        )
        self.assertEqual(
            format_nested_type_name({"nesting_mode": "set"}),
            "set of objects",
        )
        self.assertEqual(
            format_nested_type_name({"nesting_mode": "map"}),
            "map of objects",
        )

    def test_schemas_without_functions_or_ephemerals_still_work(self):
        # The multi-provider fixture has neither section.
        items = collect_items(make_multi_provider_schema())
        functions = collect_functions(make_multi_provider_schema())
        self.assertEqual(functions, [])
        self.assertTrue(all(item.kind != "ephemeral" for item in items))


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
    test_classes = (
        TestFormatType,
        TestCollectAndSearch,
        TestFiltering,
        TestGrouping,
        TestPrintedOutput,
        TestNestedBlocks,
        TestExpandedFeatures,
    )
    for test_class in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    # verbosity=2 prints one line per test, so you can see each result.
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # .wasSuccessful() is True only when every single test passed.
    return 0 if result.wasSuccessful() else 1


# ---------------------------------------------------------------------------
# Program entry point (this MUST stay at the very bottom of the file)
# ---------------------------------------------------------------------------
# When you run "python3 tfp.py ...", Python sets the special
# variable __name__ to "__main__", so the code below runs.
# When another file imports this one (as the tests could), __name__ is the
# file's name instead, and nothing runs automatically.  That is exactly
# what we want: importing should never start the program by itself.
#
# sys.exit(main()) hands our exit code back to the shell:
# 0 = success, anything else = something went wrong.

if __name__ == "__main__":
    sys.exit(main())
