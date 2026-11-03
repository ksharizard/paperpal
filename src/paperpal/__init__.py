"""PaperPal - a chapter-wise practice hub for Manipal previous year papers."""

from __future__ import annotations

import argparse
from pathlib import Path

from .build import build_bank, print_stats
from .extract import extract_images, extract_papers, print_extract, print_images
from .scrape import PAPERS_DIR, SEMESTERS, print_scrape, scrape_papers
from .serve import serve


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="paperpal",
        description="Extract, build and browse Manipal previous year question banks.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("build", help="Validate the bank and index the local paper archive")
    subparsers.add_parser("extract", help="Extract text from every PDF in papers/")
    images_parser = subparsers.add_parser(
        "images", help="Extract embedded diagrams from every PDF in papers/"
    )
    images_parser.add_argument("--min-width", type=int, default=80, help="skip images narrower than this")
    images_parser.add_argument("--min-height", type=int, default=60, help="skip images shorter than this")
    subparsers.add_parser("stats", help="Print question counts by chapter")

    scrape_parser = subparsers.add_parser(
        "scrape", help="Download first-year papers from pyq.arzvak.com"
    )
    scrape_parser.add_argument(
        "--semester",
        action="append",
        choices=SEMESTERS,
        help="semester to fetch (repeatable; defaults to every category on the site)",
    )
    scrape_parser.add_argument("--workers", type=int, default=4, help="parallel downloads")
    scrape_parser.add_argument("--limit", type=int, default=0, help="cap the number of papers")
    scrape_parser.add_argument("--dry-run", action="store_true", help="print paths without downloading")
    scrape_parser.add_argument("--overwrite", action="store_true", help="re-download existing files")
    scrape_parser.add_argument("--out", type=lambda value: Path(value).expanduser(), default=PAPERS_DIR)

    serve_parser = subparsers.add_parser("serve", help="Run the local web app")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "build":
        bank = build_bank()
        print(
            f"Built {bank['meta']['questionCount']} questions -> "
            "data/bank.json (manifest), data/bank-parts/ and data/archive.json"
        )
    elif args.command == "extract":
        print_extract(extract_papers())
    elif args.command == "images":
        print_images(extract_images(min_width=args.min_width, min_height=args.min_height))
    elif args.command == "stats":
        print_stats(build_bank())
    elif args.command == "scrape":
        print_scrape(
            scrape_papers(
                out_dir=args.out,
                semesters=tuple(args.semester) if args.semester else None,
                workers=args.workers,
                limit=args.limit,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
            )
        )
    elif args.command == "serve":
        serve(port=args.port, open_browser=not args.no_browser)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
