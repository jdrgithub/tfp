# Code guide

Architecture and main parts of `tfp.py`. For install and usage, see
[README.md](README.md).

## File map

Top to bottom in `tfp.py`:

1. **Constants** — column headers, kind maps, block purpose text
2. **Types** — `ProviderItem`, `FunctionItem`, `FunctionParameter`
3. **Loading** — run Terraform, read a JSON file, or use demo data
4. **Collecting** — turn schema JSON into item/function lists
5. **Search / filter** — name match, kind filter, `--show` field filters
6. **Printing** — tables, nested blocks, item and function details
7. **Handlers / CLI** — `list`, `search`, `show`, `provider`; `main()`
8. **Tests** — fake schemas and `unittest` classes at the end of the file

## Types

- **`ProviderItem`** — one resource, data source, ephemeral, or provider
  config: name, kind, provider, block, optional description / deprecation /
  schema version
- **`FunctionItem`** — one provider-defined function: parameters, return
  type, summary
- **`kind`** — `resource`, `datasource`, `ephemeral`, `provider`, or
  (separately) functions via `FunctionItem`

## Data flow

```text
schema JSON (Terraform / file / --demo)
        |
        v
  collect_items / collect_functions / collect_provider_configs
        |
        v
  search / kind / --show filtering
        |
        v
  print_*  ->  terminal
```

## Entry points

**Load**

- `load_schema()` — demo, `--schema-file`, or `terraform providers schema -json`
- `run_terraform_schema_command()` / `load_schema_from_file()`

**Collect**

- `collect_items()` — resources, data sources, ephemerals
- `collect_provider_configs()` — `provider "NAME" { ... }` schemas
- `collect_functions()` — provider-defined functions

**Filter**

- `search_items()` / `find_exact_items()` — name lookup
- `filter_attributes()` — `--show all|required|optional|attributes`
- `group_items_by_provider()` — section headings and list order

**Print**

- `print_items_list()` — numbered list by provider
- `print_attribute_table()` — NAME / TYPE / REQ / OPT / COMP / SENS / DESCRIPTION
- `print_block()` / `print_one_block_type()` — nested blocks (config, filter, timeouts)
- `print_nested_attributes()` — `nested_type` fields under a parent
- `print_item_details()` / `print_function_details()` — one item or function

**CLI**

- `handle_list()` / `handle_search()` / `handle_show()` / `handle_provider()`
- `build_parser()` / `main()`

## Tests

```bash
python3 tfp.py --self-test
```

Suites at the bottom of `tfp.py` cover type formatting, collect/search,
`--show` filters, provider grouping, printed layout, nested-block text,
provider config, nested attributes, functions, and ephemerals. Fixtures:
`make_fake_schema()`, `make_multi_provider_schema()`.

## Conventions

- One job per function
- Standard library only
- Longer comments live in `tfp.py`
