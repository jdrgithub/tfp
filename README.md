# Terraform Provider Viewer

`tfp.py` is a Python script for exploring
Terraform provider settings from the terminal.

Instead of digging through provider web pages or huge JSON files by hand,
you can:

1. List every resource and data source in a provider.
2. Search resources and data sources by a keyword, for example `ec2` or `bucket`.
3. View one exact resource or data source, for example `aws_instance`.
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
  required / optional filtering at each level.
- **Output:** plain terminal text in aligned columns. Each provider is
  named once, as a heading at the top of its section, and arguments are
  shown with separate Name, Type, Required, Optional, Computed, and
  Description columns.
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
```

Notes on these examples:

- `search fake --show required` lists the matches first, then asks you to
  type a number to view one of them. Type `q` to quit without viewing.
- In `show fake_server --show required`, you can see both the required
  top-level argument (`name`) and the required nested block
  (`disk { gb }`). The `optional` and `attributes` views filter that
  nested block out, which is the correct behaviour.

Verified behaviour:

- All 27 built-in tests pass.
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
------  ----------  -----------
1       datasource  fake_image
2       resource    fake_bucket
3       resource    fake_server

Total: 3
```

The numbering keeps counting across providers, and typing a number
after a search always selects the item printed beside that number.

`show` prints the provider once at the top, then one table per block.
Each argument / attribute has its own columns for its name, its type,
and whether it is required, optional, and/or computed — the word itself
appears in the matching column. A value that is optional *and* computed
gets a word in both columns. Long types and descriptions wrap inside
their own column, and sensitive / deprecated notes are included in the
description:

```text
Provider: fake

fake_server  [resource]
=======================
Showing: all

Arguments / attributes:
NAME        TYPE           REQUIRED  OPTIONAL  COMPUTED  DESCRIPTION
----------  -------------  --------  --------  --------  -----------------------
id          string                             computed
ip_address  string                             computed
name        string         required                      The name of the server.
size        string                   optional            How big the server is.
tags        map of string            optional  computed
Nested blocks:
  - disk : block (list, min: 1)
    Arguments / attributes:
    NAME   TYPE    REQUIRED  OPTIONAL  COMPUTED  DESCRIPTION
    -----  ------  --------  --------  --------  -----------
    gb     number  required
    label  string            optional
```

Nested blocks keep their own indented table with the same columns, so
the nesting stays visible.

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
| `list [--kind all\|resources\|datasources]` | List resources and/or data sources (default: `all`) |
| `search KEYWORD [--show all\|required\|optional\|attributes]` | List names containing `KEYWORD` (case-insensitive). With `--show`, pick a match by number and view it. Without `--show`, only the list is printed. |
| `show NAME [--kind resource\|datasource] [--show ...]` | View one exact name (default `--show`: `all`). `--kind` disambiguates a name that exists as both a resource and a data source; without it, both are shown. |
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

### One nuance worth knowing

A value can be both **optional** and **computed** at the same time: you
may set it, and Terraform may also fill it in for you. Such a value (for
example `tags` in the demo data) legitimately appears in both the
`optional` view and the `attributes` view. That is real provider
behaviour, not a bug.

## How the code is organized

The file is grouped into small sections, each with its own job. This is a
good map for reading the code from top to bottom:

- **Loading the schema** — getting the raw data
  - `run_terraform_schema_command()` — runs `terraform providers schema -json`
  - `load_schema_from_file()` — reads a saved JSON file
  - `load_schema()` — picks exactly one of the above (or the demo data)
- **Reading the schema** — finding things inside the raw data
  - `collect_items()` — gathers every resource / data source into a tidy list
  - `search_items()` — case-insensitive keyword search
  - `find_exact_items()` — exact-name lookup
  - `group_items_by_provider()` — groups items by provider, ordered by
    provider, then kind, then name
  - `order_items_by_provider()` — the same grouped order as one flat
    list, so printed menu numbers select the right item
  - `get_short_provider_name()` — turns
    `registry.terraform.io/hashicorp/aws` into `aws`
- **Understanding one attribute**
  - `is_required()` / `is_optional()` / `is_computed()` — tiny True/False checks
  - `filter_attributes()` — keeps only the attributes for a `--show` choice
  - `format_type()` — turns JSON types into readable text, recursively,
    e.g. `["list", "string"]` becomes `list of string`, and
    `["object", {"id": "string"}]` becomes `object({id = string})`
  - `build_attribute_row()` — builds one table row: name, type, and the
    words `required` / `optional` / `computed` in their own columns
  - `build_attribute_description()` — the description text for a row,
    including sensitive and deprecated notes
  - `block_type_matches_show()` / `block_contains_computed()` — decide
    whether nested blocks belong in the current view
- **Printing** — turning data into human-readable text
  - `print_provider_heading()` — prints `Provider: <name>` once per section
  - `print_table()` — prints an aligned table, wrapping long cells
    inside their column (`get_column_widths()`, `wrap_cell_text()`)
  - `print_items_list()` — numbered, columned list of resources / data
    sources, grouped under provider headings; returns the displayed order
  - `print_attribute_table()` — attributes in NAME / TYPE / REQUIRED /
    OPTIONAL / COMPUTED / DESCRIPTION columns
  - `print_block()` — recursive, indented printing of a block and its
    nested blocks, each with its own attribute table
  - `print_item_details()` — heading plus details for one item (the
    provider heading is printed by the caller, once per section)
  - `choose_item_from_list()` — asks the user to pick a number
- **Command handlers** — one small function per command
  - `handle_list()`, `handle_search()`, `handle_show()`
- **Command-line interface**
  - `build_parser()` — builds the `argparse` parser (`list` / `search` / `show`)
  - `main()` — the starting point; loads data once and calls one handler
- **Tests (at the end of the file)**
  - `make_fake_schema()` — a tiny fake provider containing the interesting
    cases: required, optional, computed, optional-and-computed, a nested
    block with a required value inside it, a data source, and a second
    resource so searches can return more than one match
  - `make_multi_provider_schema()` — a test-only schema with two
    providers sharing a resource name, used by the grouping tests
  - `TestFormatType`, `TestCollectAndSearch`, `TestFiltering`,
    `TestGrouping`, `TestPrintedOutput` — `unittest` test classes; the
    last two capture printed output and check provider grouping,
    menu numbering, and column alignment
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

This runs 27 tests covering type formatting, collecting items from a
schema, keyword and exact search, required / optional / attributes
filtering (including nested blocks), provider grouping, menu
numbering, and column layout. The tests use fake schemas, so they pass
on any machine, with or without Terraform installed.

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
