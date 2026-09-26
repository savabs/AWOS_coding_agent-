# orders_export_filters

Feature task on a ~900-line stdlib order-management CLI (`ordertool`): add `--from/--to/--status` filters to `export`.
The agent must read models, storage, dates/money utils, export and the argparse CLI, then change at least three modules (date parsing helper, export filtering, CLI wiring + error handling).
Hidden tests run the CLI end to end in a subprocess: inclusive boundaries (23:59:59 on the `--to` day), combined filters, `--output`, unchanged unfiltered output, and clean errors (no traceback) for bad dates like `2024-02-30` and unknown statuses.
It is realistic because it is the kind of small cross-cutting feature request a user files against an existing tool, where the easy mistake (comparing datetimes to midnight, letting `ValueError` escape) looks right at a glance.
