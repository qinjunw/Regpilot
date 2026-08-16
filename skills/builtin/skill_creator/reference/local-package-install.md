# Local Skill Package Install Workflow

This reference defines the RegPilot-local workflow for installing a skill package that already exists on the operator's machine.

## Accepted Package Shape

Minimum:

```text
skill-name/
  SKILL.md
```

Optional resources:

```text
skill-name/
  agents/
  reference/
  references/
  scripts/
  assets/
```

RegPilot preserves these resource directories during local install, but it does not execute scripts.

## Collection Directories

A directory that contains multiple skill packages is a collection, not a package. If inspection returns `location=collection`, present the candidate `skill_id`, title, status, and path values and ask the operator to choose one package.

Do not install a collection directory directly.

## Classification

- `ai_workflow`: a guidance skill loaded into model context through `regpilot_load_skill`.
- `action_skill`: a controlled executable skill that must include `regpilot_skill: true` and a fenced `json regpilot_manifest` block.

If a local package has only standard `name` and `description` frontmatter, classify it as `ai_workflow`.

`status=local_source` means the package is visible on disk but has not been copied into `skills/installed`. Install it before enabling it.

## Review Rules

During inspection, report:

- package path
- skill id/name
- title
- description
- skill type
- resources found under `reference(s)/`, `scripts/`, `assets/`, and `agents/`
- validation errors and warnings

When scripts exist, mention that scripts are installed as files only and remain untrusted.

## Install Rules

1. Inspect first.
2. Validate second.
3. Install to `skills/installed`.
4. Leave `enabled=false`.
5. Enable only after the operator explicitly asks.
6. Load only after enabling.

Do not use remote download, Git clone, package manager install, or arbitrary command execution in this workflow.
