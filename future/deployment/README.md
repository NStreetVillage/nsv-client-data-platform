# Deployment Notes - Future Server Setup

Deployment is not required while the app is still being tested on a laptop.

For now, keep the local workflow simple: run the FastAPI app from the backend
folder and use the local SQLite database for prototype testing. A Docker/dev
shell setup is worth revisiting when the in-house server environment is known.

Future server-readiness work:

- Decide whether the in-house server runs Windows or Linux.
- Move secrets and paths into environment variables.
- Use PostgreSQL or another approved managed database instead of local SQLite.
- Add authentication, roles, and audit logging before broad staff access.
- Define backup, restore, and access-control procedures.
- Consider Docker once the deployment target is clear.

Questions for Tim:

- What server environment is available?
- What operating system does the server run?
- Is there an existing database server?
- Are PostgreSQL or SQL Server available?
- How are backups handled?
- Who manages server access?
- Is the server approved for client-related data?
