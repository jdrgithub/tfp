# Terraform Provider Viewer

Terminal tool for exploring Terraform provider schemas. Runs
`terraform providers schema -json` in the current folder.

## Requirements

- Python 3.10+
- Terraform installed
- A Terraform project that uses the provider you want to inspect
- `terraform init` already run in that project

## Install

One copy of the script; run it from any Terraform folder.

```bash
chmod +x /home/jdr/jdrgithub/tfp/tfp.py
mkdir -p ~/.local/bin
ln -s /home/jdr/jdrgithub/tfp/tfp.py ~/.local/bin/tfview
```

If `tfview` is not found, add `~/.local/bin` to your `PATH`. Replace the
path above with your clone location.

Alias alternative:

```bash
alias tfview='python3 /home/jdr/jdrgithub/tfp/tfp.py'
```

`--demo`, `--schema-file`, and `--self-test` go **before** the subcommand
(e.g. `tfview --demo list`).

## List

Lists schema names, grouped by provider. `--kind` narrows the list.

Shows: numbered names (resources, data sources, ephemerals; or functions
with `--kind functions`).

```bash
cd ~/some/terraform/project
terraform init
tfview list
tfview list --kind resources
tfview list --kind datasources
tfview list --kind ephemerals
tfview list --kind functions
```

Demo:

```bash
tfview --demo list
tfview --demo list --kind datasources
tfview --demo list --kind functions
tfview --demo list --kind ephemerals
```

## Search

Finds names containing a keyword. With `--show`, pick a match by number
and open its details.

Shows: matching names; with `--show`, the chosen item’s schema.

```bash
tfview search ec2
tfview search bucket --show optional
```

Demo:

```bash
tfview --demo search fake
tfview --demo search fake --show required
```

## Show

Shows one exact name. Use `--kind` when the same name exists as more than
one kind. `--show` filters fields: `all`, `required`, `optional`,
`attributes`.

Shows: that item’s arguments, attributes, and nested blocks (as
applicable).

```bash
tfview show aws_instance
tfview show aws_instance --show required
tfview show aws_instance --show optional
tfview show aws_instance --show attributes
tfview show aws_ami --kind datasource --show all
tfview show cidr_contains --kind function
tfview show temporary_password --kind ephemeral
```

Demo:

```bash
tfview --demo show fake_server --show all
tfview --demo show fake_server --show required
tfview --demo show fake_image --kind datasource --show all
tfview --demo show cidr_contains --kind function
tfview --demo show temporary_password --kind ephemeral
```

## Provider

Shows the schema for `provider "NAME" { ... }` configuration.

Shows: that provider’s configuration arguments and nested blocks.

```bash
tfview provider aws
tfview provider aws --show required
```

Demo:

```bash
tfview --demo provider fake
```

## Schema file

Reads a saved schema JSON instead of running Terraform.

Shows: same as the other commands, from the file.

```bash
terraform providers schema -json > schema.json
tfview --schema-file schema.json list
```

## Command reference

| Command | Purpose |
| --- | --- |
| `list [--kind all\|resources\|datasources\|ephemerals\|functions]` | List names |
| `search KEYWORD [--show ...]` | Find names; optional open details |
| `show NAME [--kind resource\|datasource\|ephemeral\|function] [--show ...]` | Show one item |
| `provider NAME [--show ...]` | Show provider config schema |
| `--demo` | Built-in fake provider (no Terraform) |
| `--schema-file PATH` | Use a saved JSON schema |
| `--self-test` | Run built-in tests |
| `--help` | Help |

Code layout: [CODE_GUIDE.md](CODE_GUIDE.md).
