# Security policy

Report security-sensitive problems privately to the maintainer rather than publishing command contents, credentials, private paths, or patient/sample information in a public issue. Until a dedicated security contact is published, open a minimal issue asking for a private reporting channel without disclosing the vulnerability.

## Trust model

CryoPal_tomo launches local and Slurm commands with the permissions of the current user. Custom jobs, shortcuts, environment activation commands, executable overrides, and Slurm shell preambles are executable code. Only import project or settings files from trusted sources, inspect command previews, and never store secrets directly in project files.

Built-in structured Slurm header fields reject multiline values. Explicit custom scripts and shell preambles intentionally retain shell semantics.

Supported security fixes are provided for the latest released version. No 1.0 security-support window has yet been declared.
