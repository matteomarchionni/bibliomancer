# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately with
GitHub's **Report a vulnerability** button (Security tab of this repository). You will get
an answer as soon as possible; please allow time for a fix before disclosing it.

## Scope

BiblioMancer runs entirely on your computer. Its only network traffic is to LM Studio on
`127.0.0.1`; it reads your PDF library, writes notes and a database into its data folder,
and rewrites the export folder you choose. Relevant reports include, for example: a crafted
PDF or scout brief that makes BiblioMancer write outside its folders or run code, or a way
the export folder could leak data the user did not choose to export.

Only the latest release is supported.
