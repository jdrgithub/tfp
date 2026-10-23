# Terraform Provider Viewer

`tfp.py` is a Python script for exploring
Terraform provider settings from the terminal.

Instead of digging through provider web pages or huge JSON files by hand,
you can:

1. List resources, data sources, ephemeral resources and provider-defined functions.
2. Search by a keyword, for example `ec2` or `bucket`.
3. View one exact item, for example `aws_instance`, or a provider’s own
   configuration with `provider aws`.
4. Choose what to view for it:
   - `all` — every argument
   - `required` — only arguments you **must** set
   - `optional` — only arguments you **may** set
   - `attributes` — only values Terraform gives back to you (computed)

The script is designed to be clear and readable:

- Only Python's standard library is used, so there is nothing to install.
- Extensive comments are provided throughout the code.
- Each function does one small, clear job.
- Tests live at the end of the file and can be run with `--self-test`.

## Design choices

This first version uses the following approach (option **A** for each
clarifying question):

- **Data source:** run `terraform providers schema -json` and parse its output.
  This is the most accurate source because it comes from Terraform and the
  provider itself.
- **Interface:** command-line arguments, for example
  `search ec2 --show required` and `show aws_instance --show attributes`.
- **Search:** list every matching resource and data source, then let the
  user pick one by number when details were requested.
- **Nested blocks:** show nested blocks recursively, indented, with
  required / optional filtering at each level, grouped by purpose and
  explained in plain language (see “How nested blocks are presented”).
- **Output:** plain terminal text in aligned columns. Each provider is
  named once, as a heading at the top of its section. Flag columns use
  short headings (`REQ` / `OPT` / `COMP` / `SENS`) with an `X` marker,
  and each column is sized to the widest entry so every row lines up.
- **Tests:** a small built-in fake schema with `unittest` tests at the end
  of the file, run via `--self-test`.

Bonus: a saved schema file can also be read with `--schema-file`.

## Requirements

- Python 3.10 or newer
- For real provider data:
  - Terraform installed
  - A Terraform project folder (containing `.tf` files) that uses the
    provider you want to explore
  - `terraform init` already run in that folder

No Terraform? No problem while learning. The script includes a tiny fake
provider for practice (`--demo`) and self-tests that need no Terraform
at all (`--self-test`).

## Try it now (demo data, no Terraform needed)

```bash
python3 tfp.py --self-test
python3 tfp.py --demo list
python3 tfp.py --demo list --kind datasources
python3 tfp.py --demo search fake
python3 tfp.py --demo search fake --show required
python3 tfp.py --demo show fake_server --show all
python3 tfp.py --demo show fake_server --show required
python3 tfp.py --demo show fake_server --show optional
python3 tfp.py --demo show fake_server --show attributes
python3 tfp.py --demo provider fake
python3 tfp.py --demo list --kind functions
python3 tfp.py --demo show cidr_contains --kind function
python3 tfp.py --demo list --kind ephemerals
```

Notes on these examples:

- `search fake --show required` lists the matches first, then asks you to
  type a number to view one of them. Type `q` to quit without viewing.
- In `show fake_server --show required`, you can see both the required
  top-level argument (`name`) and the required nested block
  (`disk { gb }`). The `optional` and `attributes` views filter that
  nested block out, which is the correct behaviour.

Verified behaviour:

- All 42 built-in tests pass.
- Results are grouped by provider: each provider name is printed once,
  as a heading at the top of its section, instead of being repeated on
  every row. A new heading only appears when the output moves to a
  different provider.
- Arguments and attributes are shown in aligned columns (see
  “Output format” below).
- `required` correctly shows `name` plus the required nested
  `disk { gb }` block, while `optional` / `attributes` filter it out.
- Without `--demo`, on a machine with no Terraform installed, the script
  stops with a friendly error message explaining the problem and
  suggesting `--demo` or `--self-test`, instead of printing a traceback.

## Output format

`list` and `search` print one section per provider, with the items in
columns under the provider heading:

```text
Listing: all
------------

Provider: fake
NUMBER  KIND        NAME
------  ----------  -------------------
1       datasource  fake_image
2       ephemeral   temporary_password
3       resource    fake_bucket
4       resource    fake_server

Total: 4
```

The numbering keeps counting across providers, and typing a number
after a search always selects the item printed beside that number.

`show` prints the provider once at the top, then one table per block.
Flag columns use short headings and an `X` marker (blank means no).
A value that is optional *and* computed gets an `X` in both `OPT` and
`COMP`. Column widths grow to the widest entry in the table so short
and long names stay aligned. Only the description column wraps.

```text
Provider: fake

fake_server  [resource]
=======================
Schema version: 0
Showing: all

Arguments / attributes:
Markers: X = yes  (REQ = required, OPT = optional, COMP = computed, SENS = sensitive)
NAME        TYPE           REQ  OPT  COMP  SENS  DESCRIPTION
----------  -------------  ---  ---  ----  ----  -----------------------
endpoint    object              X    X           Endpoint settings.
id          string                   X
ip_address  string                   X
name        string         X                     The name of the server.
size        string              X                How big the server is.
tags        map of string       X    X

Nested attributes under "endpoint" (object):
Markers: X = yes  (REQ = required, OPT = optional, COMP = computed, SENS = sensitive)
NAME    TYPE    REQ  OPT  COMP  SENS  DESCRIPTION
------  ------  ---  ---  ----  ----  ------------
host    string       X    X           Host name.
port    number       X    X           Port number.
secure  bool         X                Use TLS.
```

Nested blocks and nested attributes keep their own indented tables with
the same columns, so the nesting stays visible.

## How nested blocks are presented

A nested block is configuration written as `name { ... }` instead of
`name = value`. Providers describe these in their schema with raw
cardinality data such as `set, min: 0`. The script translates that into
plain language and groups blocks by purpose:

- **Nested configuration blocks** — ordinary provider-defined blocks.
- **Lookup filter blocks** — a data-source `filter` block.
- **Provider operation timeouts** — a `timeouts` block.

Each block shows whether it is optional or required, whether it may be
repeated, what it is for, and — for `filter` and `timeouts` — a small
HCL example. If a block is optional, its fields are headed
“Fields — required only if you write this block:”, because a field
marked `required` there is only required once you choose to use that
block.

For example, `aws_security_group` used as a data source has both of
these special blocks. Neither is a property of the security group
itself:

- `filter` narrows the lookup for an existing security group, using
  AWS filter names such as `tag:Name` or `vpc-id`. It does not change
  the security group.
- `timeouts` controls how long Terraform waits for the read operation.
  Most users never need to set it.

```hcl
data "aws_security_group" "example" {
  filter {
    name   = "tag:Name"
    values = ["web-server"]
  }

  timeouts {
    read = "5m"
  }
}
```

In the output, those blocks look like this (abbreviated):

```text
Lookup filter blocks:
---------------------
  filter
  Optional block; may be repeated. Each block must be unique.
  Purpose:
    Narrows the data-source lookup using provider filter names, such as
    "tag:Name" or "vpc-id". This does not change the object itself.
  Written as:
    filter {
      name   = "tag:Name"
      values = ["example"]
    }
  Fields — required only if you write this block:
    NAME    TYPE           REQ  OPT  COMP  SENS  DESCRIPTION
    ------  -------------  ---  ---  ----  ----  -----------
    name    string         X
    values  set of string  X

Provider operation timeouts:
----------------------------
  timeouts
  Optional block; may appear at most once.
  Purpose:
    Controls how long Terraform waits for provider operations. Field values are
    durations, such as "5m". This is not a property of the object itself, and
    most users do not need to set it.
  Written as:
    timeouts {
      read = "5m"
    }
  Fields — required only if you write this block:
    NAME  TYPE    REQ  OPT  COMP  SENS  DESCRIPTION
    ----  ------  ---  ---  ----  ----  -----------
    read  string       X
```

For a data source, the top-level table is also introduced as
“Lookup arguments and returned attributes”, with a note that
`optional` values can be used to find the object and `computed` values
are returned from the object that was found.

## Provider configuration, functions and ephemerals

View a provider’s own configuration (the arguments inside
`provider "aws" { ... }`):

```bash
python3 tfp.py --demo provider fake
python3 tfp.py provider aws --show required
```

```text
Provider: fake

Provider configuration  [provider]
==================================
Showing: all

Configuration arguments:
Markers: X = yes  (REQ = required, OPT = optional, COMP = computed, SENS = sensitive)
NAME                 TYPE            REQ  OPT  COMP  SENS  DESCRIPTION
-------------------  --------------  ---  ---  ----  ----  -----------------------------
access_key           string               X          X     Static access key.
region               string               X                AWS region to use.
shared_config_files  list of string       X                Paths to shared config files.
```

List and show provider-defined functions and ephemeral resources:

```bash
python3 tfp.py --demo list --kind functions
python3 tfp.py --demo show cidr_contains --kind function
python3 tfp.py --demo list --kind ephemerals
python3 tfp.py --demo show temporary_password --kind ephemeral
```

Ephemeral resources print a note that their values exist only during the
Terraform operation and are not saved in state or plan files.

`nested_type` attributes (provider-framework nested objects) are expanded
under their parent with the same columns, for example
`Nested attributes under "endpoint" (object):`. That is separate from
`block_types`, which are nested HCL blocks written as `name { ... }`.

## Running it from any Terraform folder

You do not need a copy of the script in each Terraform folder. Keep
**one copy** of the script in one place, and run it *from* each
Terraform folder.

This works because the script launches `terraform providers schema -json`
as a subprocess, and subprocesses inherit your current working
directory. So Terraform runs wherever you happen to be standing, while
the script itself lives elsewhere.

### Easiest: call it by its full path

```bash
cd ~/some/terraform/project
terraform init
python3 /home/jdr/jdrgithub/tfp/tfp.py list
python3 /home/jdr/jdrgithub/tfp/tfp.py search ec2
python3 /home/jdr/jdrgithub/tfp/tfp.py show aws_instance --show required
```

That works from any folder. The full path is just tedious to type.

> If you move this repository, replace
> `/home/jdr/jdrgithub/tfp/tfp.py` in the commands below
> with the script's new location.

### Recommended: one symlink so you can type `tfview` anywhere

A symlink is a pointer, not a copy — edits to the script in this repo
are instantly live everywhere:

```bash
chmod +x /home/jdr/jdrgithub/tfp/tfp.py
mkdir -p ~/.local/bin
ln -s /home/jdr/jdrgithub/tfp/tfp.py ~/.local/bin/tfview
```

Then from any Terraform folder:

```bash
cd ~/some/terraform/project
terraform init
tfview list
tfview search bucket --show optional
tfview show aws_instance --show attributes
```

If `tfview` is not found, `~/.local/bin` may not be on your `PATH` yet.
Add it once (this system uses `zsh`):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Alternative: a shell alias

Same result, different mechanism — pick one, not both:

```bash
echo "alias tfview='python3 /home/jdr/jdrgithub/tfp/tfp.py'" >> ~/.zshrc
source ~/.zshrc
```

The symlink is slightly preferable: it works in scripts and other
shells, not just interactive `zsh`.

### Things worth knowing

- `terraform init` is still per-folder (it downloads providers into
  that project) — that is Terraform's own behaviour, unrelated to this
  script. The script itself is never installed per folder.
- Relative paths still behave as you would expect:
  `tfview --schema-file schema.json list` looks for `schema.json` in
  your current folder, not next to the script.
- `--demo` and `--self-test` work from anywhere too:
  `tfview --self-test`.

## Use it for real

Run the script inside a Terraform project folder, after `terraform init`:

```bash
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
```

If nothing matches an exact name given to `show`, the script falls back
to a keyword search and offers similar names to pick from.

### Using a saved schema file

If you prefer to save Terraform's JSON output yourself, you can read
that file instead of asking Terraform to produce it again:

```bash
terraform providers schema -json > schema.json
python3 tfp.py --schema-file schema.json list
```

Note: global options such as `--demo`, `--schema-file`, and
`--self-test` go **before** the command word (`list`, `search`, `show`).

## Command reference

| Command | What it does |
| --- | --- |
| `list [--kind all\|resources\|datasources\|ephemerals\|functions]` | List items (default `all` = resources, datasources, ephemerals; use `--kind functions` for provider functions) |
| `search KEYWORD [--show ...]` | List names containing `KEYWORD` (case-insensitive). With `--show`, pick a match by number and view it. |
| `show NAME [--kind resource\|datasource\|ephemeral\|function] [--show ...]` | View one exact name (default `--show`: `all`) |
| `provider NAME [--show ...]` | View a provider’s own configuration arguments |
| `--demo ...` | Use the tiny built-in fake provider instead of Terraform |
| `--schema-file PATH ...` | Read a saved `terraform providers schema -json` file |
| `--self-test` | Run the built-in tests at the end of the file, then exit |
| `--help` | Show help with examples |

## Key Terraform words

- **Resource:** a thing Terraform creates / manages, e.g. `aws_instance`.
- **Data source (datasource):** a thing Terraform only reads, e.g. `aws_ami`.
  In Terraform's JSON this is spelled `data_source_schemas`.
- **Argument:** a value you can write inside the block in your `.tf` file.
  Arguments are either `required` or `optional`.
- **Attribute:** in this script, a value Terraform gives back to you after
  it reads or creates the thing. In the JSON this is called `computed`.
- **Block:** a nested group of arguments, e.g. `ebs_block_device { ... }`
  inside `aws_instance`. In the JSON these are `block_types`.
- **Ephemeral resource:** like a resource/data source whose values exist
  only during a Terraform operation and are not saved in state or plan files.
- **Provider-defined function:** a function supplied by the provider and
  callable from Terraform expressions.

### One nuance worth knowing

A value can be both **optional** and **computed** at the same time: you
may set it, and Terraform may also fill it in for you. Such a value (for
example `tags` in the demo data) legitimately appears in both the
`optional` view and the `attributes` view — with an `X` under both
`OPT` and `COMP`. That is real provider behaviour, not a bug.

## How the code is organized

The file is grouped into small sections, each with its own job. This is a
good map for reading the code from top to bottom:

- **Loading the schema** — getting the raw data
  - `run_terraform_schema_command()` — runs `terraform providers schema -json`
  - `load_schema_from_file()` — reads a saved JSON file
  - `load_schema()` — picks exactly one of the above (or the demo data)
- **Reading the schema** — finding things inside the raw data
  - `collect_items()` — gathers resources, data sources and ephemerals
  - `collect_provider_configs()` — gathers each provider’s own config block
  - `collect_functions()` — gathers provider-defined functions
  - `search_items()` / `search_functions()` — case-insensitive keyword search
  - `find_exact_items()` / `find_exact_functions()` — exact-name lookup
  - `group_items_by_provider()` — groups items by provider, ordered by
    provider, then kind, then name
  - `order_items_by_provider()` — the same grouped order as one flat
    list, so printed menu numbers select the right item
  - `get_short_provider_name()` — turns
    `registry.terraform.io/hashicorp/aws` into `aws`
- **Understanding one attribute**
  - `is_required()` / `is_optional()` / `is_computed()` / `is_sensitive()` —
    tiny True/False checks
  - `filter_attributes()` — keeps only the attributes for a `--show` choice
  - `format_type()` / `format_attribute_type()` / `format_nested_type_name()` —
    turn JSON types (including `nested_type`) into readable text
  - `build_attribute_row()` — builds one table row with `X` markers under
    `REQ` / `OPT` / `COMP` / `SENS`
  - `build_attribute_description()` — the description text for a row,
    including deprecated notes
  - `block_type_matches_show()` / `block_contains_computed()` — decide
    whether nested blocks belong in the current view
  - `describe_block_cardinality()` — translates raw schema cardinality
    (`set`, `min_items`, `max_items`) into plain language such as
    “Optional block; may be repeated. Each block must be unique.”
  - `classify_block()` / `get_block_purpose()` /
    `build_block_example_lines()` — classify blocks as configuration,
    data-source lookup filters, or provider operation timeouts, explain
    what they are for, and build small HCL examples for `filter` and
    `timeouts`
- **Printing** — turning data into human-readable text
  - `print_provider_heading()` — prints `Provider: <name>` once per section
  - `print_table()` — prints an aligned table, wrapping long cells
    inside their column (`get_column_widths()`, `wrap_cell_text()`)
  - `print_items_list()` — numbered, columned list of resources / data
    sources, grouped under provider headings; returns the displayed order
  - `print_attribute_table()` — attributes in NAME / TYPE / REQ / OPT /
    COMP / SENS / DESCRIPTION columns, with an `X`-marker legend
  - `print_nested_attributes()` — expands `nested_type` fields under
    their parent attribute
  - `print_block()` — recursive, indented printing of a block and its
    nested blocks, grouped by purpose (configuration, lookup filters,
    timeouts), each explained in plain language with its own table
  - `print_one_block_type()` — one nested block: name, cardinality,
    purpose, optional HCL example, and its fields
  - `print_item_details()` / `print_item_metadata()` — heading, optional
    description/deprecation/schema-version lines, and details
  - `print_function_details()` — function summary, parameters and return type
  - `choose_item_from_list()` — asks the user to pick a number
- **Command handlers** — one small function per command
  - `handle_list()`, `handle_search()`, `handle_show()`, `handle_provider()`
- **Command-line interface**
  - `build_parser()` — builds the `argparse` parser
    (`list` / `search` / `show` / `provider`)
  - `main()` — the starting point; loads data once and calls one handler
- **Tests (at the end of the file)**
  - `make_fake_schema()` — a tiny fake provider covering provider config,
    nested blocks, nested attributes, metadata, functions and ephemerals
  - `make_multi_provider_schema()` — a test-only schema with two
    providers sharing a resource name, used by the grouping tests
  - `TestFormatType`, `TestCollectAndSearch`, `TestFiltering`,
    `TestGrouping`, `TestPrintedOutput`, `TestNestedBlocks`,
    `TestExpandedFeatures` — `unittest` test classes covering grouping,
    column alignment with `X` markers, nested-block explanations, and
    the new provider/function/ephemeral/nested-type features
  - `run_self_tests()` — runs every test class and returns an exit code

Data flows through the program in one direction:

```text
Terraform JSON (or demo / file)
        |
        v
  collect_items()  ->  list of ProviderItem
        |
        v
  search / exact-match / kind filtering
        |
        v
  filter_attributes() + nested-block filtering
        |
        v
  print_block()  ->  indented text in the terminal
```

## Running the tests

```bash
python3 tfp.py --self-test
```

This runs 42 tests covering type formatting, collecting items from a
schema, keyword and exact search, required / optional / attributes
filtering, provider grouping, menu numbering, `X`-marker column
alignment, nested-block explanations, provider configuration, nested
attributes, provider-defined functions and ephemeral resources. The
tests use fake schemas, so they pass on any machine, with or without
Terraform installed.

## Troubleshooting

- **“Could not find the 'terraform' program”** — Terraform is not
  installed, or not on your `PATH`. Install Terraform, or practise with
  `--demo` / `--self-test`.
- **“Terraform could not produce a schema”** — run the script inside a
  Terraform project folder and make sure you have run `terraform init`
  there first.
- **“No resources or data sources were found”** — the provider may not be
  used by the code in the current folder yet. Add a resource or data
  source from that provider to your `.tf` files, run `terraform init`,
  and try again.

## Files

| File | Purpose |
| --- | --- |
| `tfp.py` | The script itself, including demo data and tests |
| `README.md` | This document |

## Addendum: Running in WSL

Yes — this script runs from WSL (Windows Subsystem for Linux), provided
Python and Terraform are installed **inside your WSL distribution**.

Nothing in `tfp.py` is specific to native Linux versus
WSL:

- It uses only Python's standard library.
- It specifies the standard Linux interpreter
  (`#!/usr/bin/env python3`).
- It launches Terraform as a program named `terraform` in the current
  folder (`terraform providers schema -json`), just as it does on Linux.

### Requirements inside WSL

Check these from your WSL terminal:

```bash
python3 --version
terraform --version
```

You need:

- **Python 3.10 or newer**
- The **Linux version of Terraform**, installed inside WSL and
  available on its `PATH`

Do not rely on a Terraform installation located only in Windows. WSL
can sometimes invoke Windows executables through interoperability, but
a native Linux Terraform binary is the reliable and predictable setup.

### Recommended WSL layout

Keep both the script and your Terraform projects in the WSL
filesystem, for example:

```text
/home/<you>/jdrgithub/tfp/tfp.py
/home/<you>/terraform/project/
```

Then:

```bash
cd ~/terraform/project
terraform init
tfview list
tfview search ec2
```

Keeping projects under `/home/...` is usually faster and avoids
permissions, executable-bit, and symbolic-link quirks that can occur on
Windows-mounted paths such as `/mnt/c/...`.

A script or Terraform project under `/mnt/c/...` will often still work,
particularly if you invoke it with `python3 ...` directly. However,
Linux Terraform and a native WSL home directory are the safer
combination.

### Symlink setup in WSL

The symlink setup described in “Running it from any Terraform folder”
also applies; substitute your actual WSL path:

```bash
chmod +x ~/jdrgithub/tfp/tfp.py
mkdir -p ~/.local/bin
ln -s ~/jdrgithub/tfp/tfp.py ~/.local/bin/tfview
```

If your WSL shell is Bash rather than Zsh, use `~/.bashrc` rather than
`~/.zshrc` when adding `~/.local/bin` to your `PATH`.

Also ensure the script retains Unix `LF` line endings if it is edited
from Windows. Windows `CRLF` endings can break execution of the
`#!/usr/bin/env python3` line, although running it explicitly with
`python3 tfp.py ...` generally avoids that particular
issue.

Even without Terraform installed in WSL, these work immediately:

```bash
tfview --self-test
tfview --demo list
```
