import argparse
from app.database import SessionLocal
from app.importer import import_file

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import NSV client data from CSV or Excel.")
    parser.add_argument("file", help="Path to CSV or Excel file")
    parser.add_argument("--source", required=True, help="Source system, such as JotForm, HMIS, HTH, or eCW")
    parser.add_argument("--program", required=True, help="Program name")

    args = parser.parse_args()

    db = SessionLocal()
    result = import_file(
        db=db,
        file_path=args.file,
        source_system=args.source,
        program_name=args.program,
    )
    db.close()

    print("Import complete.")
    for key, value in result.items():
        if key != "failed_rows":
            print(f"{key}: {value}")
