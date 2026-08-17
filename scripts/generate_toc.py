from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

DEFAULT_EXCLUDES = [
    "public-modules/**/*.html",
    "public-modules/**/*.htm",
]


@dataclass(frozen=True)
class Module:
    """
    Lightweight representation of one public module.

    Only slug, title, and primary_disciplines are required.

    source_directory and guide_filename are internal fields used when
    generating the discipline pages. They are intentionally omitted from
    __repr__.
    """

    slug: str
    title: str
    primary_disciplines: tuple[str, ...]

    source_directory: Path = field(
        default=Path("."),
        repr=False,
        compare=False,
    )

    guide_filename: str = field(
        default="module_guide.md",
        repr=False,
        compare=False,
    )

    def __repr__(self) -> str:
        return (
            "Module("
            f"slug={self.slug!r}, "
            f"title={self.title!r}, "
            f"primary_disciplines={list(self.primary_disciplines)!r}"
            ")"
        )

    @property
    def guide_path(self) -> Path:
        """
        Return the expected path to the module guide.
        """
        return self.source_directory / self.guide_filename

    @classmethod
    def from_metadata(
        cls,
        data: dict[str, Any],
        source_directory: Path,
    ) -> Module | None:
        """
        Create a Module from parsed module_metadata.yml data.

        Returns None when any required field is missing or invalid.
        """

        required_fields = (
            "slug",
            "title",
            "primary_disciplines",
        )

        if any(field_name not in data for field_name in required_fields):
            return None

        slug = data["slug"]
        title = data["title"]
        disciplines = data["primary_disciplines"]

        # Validate slug.
        if not isinstance(slug, str):
            return None

        slug = slug.strip()

        if not slug or not SLUG_PATTERN.fullmatch(slug):
            return None

        # Validate title.
        if not isinstance(title, str):
            return None

        title = title.strip()

        if not title:
            return None

        # Validate primary disciplines.
        if not isinstance(disciplines, list) or not disciplines:
            return None

        cleaned_disciplines: list[str] = []
        seen_disciplines: set[str] = set()

        for discipline in disciplines:
            if not isinstance(discipline, str):
                return None

            cleaned = discipline.strip()

            if not cleaned:
                return None

            # Remove duplicate discipline names while preserving order.
            if cleaned not in seen_disciplines:
                cleaned_disciplines.append(cleaned)
                seen_disciplines.add(cleaned)

        # Use files.module_guide when it is declared.
        # Otherwise, default to module_guide.md.
        guide_filename = "module_guide.md"

        files = data.get("files")

        if isinstance(files, dict):
            declared_guide = files.get("module_guide")

            if isinstance(declared_guide, str) and declared_guide.strip():
                candidate = Path(declared_guide.strip())

                # Do not allow absolute paths or paths escaping the module.
                if (
                    not candidate.is_absolute()
                    and ".." not in candidate.parts
                ):
                    guide_filename = candidate.as_posix()

        return cls(
            slug=slug,
            title=title,
            primary_disciplines=tuple(cleaned_disciplines),
            source_directory=source_directory,
            guide_filename=guide_filename,
        )


def load_yaml(path: Path) -> Any:
    """
    Safely load a YAML file.
    """
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_modules(
    public_modules_directory: str | Path = "public-modules",
    *,
    verbose: bool = True,
) -> list[Module]:
    """
    Read module_metadata.yml from every immediate child directory inside
    public-modules.

    A module is omitted when:

    - module_metadata.yml is missing;
    - the YAML is invalid;
    - the YAML root is not a mapping;
    - slug is missing or invalid;
    - title is missing or invalid;
    - primary_disciplines is missing or invalid;
    - another module already uses the same slug.
    """

    root = Path(public_modules_directory)

    if not root.exists():
        raise FileNotFoundError(
            f"Public modules directory does not exist: {root}"
        )

    if not root.is_dir():
        raise NotADirectoryError(
            f"Expected a directory, but received: {root}"
        )

    modules: list[Module] = []
    seen_slugs: set[str] = set()

    module_directories = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir()
        ),
        key=lambda path: path.name.casefold(),
    )

    for module_directory in module_directories:
        metadata_path = module_directory / "module_metadata.yml"

        if not metadata_path.is_file():
            if verbose:
                print(
                    f"Skipping {module_directory.name}: "
                    "module_metadata.yml was not found",
                    file=sys.stderr,
                )

            continue

        try:
            metadata = load_yaml(metadata_path)

        except (OSError, yaml.YAMLError) as error:
            if verbose:
                print(
                    f"Skipping {module_directory.name}: {error}",
                    file=sys.stderr,
                )

            continue

        if not isinstance(metadata, dict):
            if verbose:
                print(
                    f"Skipping {module_directory.name}: "
                    "metadata must be a YAML mapping",
                    file=sys.stderr,
                )

            continue

        module = Module.from_metadata(
            metadata,
            source_directory=module_directory,
        )

        if module is None:
            if verbose:
                print(
                    f"Skipping {module_directory.name}: "
                    "slug, title, or primary_disciplines "
                    "is missing or invalid",
                    file=sys.stderr,
                )

            continue

        if module.slug in seen_slugs:
            if verbose:
                print(
                    f"Skipping {module_directory.name}: "
                    f"duplicate module slug {module.slug!r}",
                    file=sys.stderr,
                )

            continue

        seen_slugs.add(module.slug)
        modules.append(module)

    return modules


def slugify(value: str) -> str:
    """
    Convert a discipline name into a directory-safe slug.

    Examples:

        Data Science
        -> data-science

        Science, Technology, and Society
        -> science-technology-and-society
    """

    normalized = unicodedata.normalize("NFKD", value)

    ascii_value = (
        normalized
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        ascii_value.lower(),
    ).strip("-")

    if not slug:
        raise ValueError(
            f"Cannot create a slug from discipline {value!r}"
        )

    return slug


def group_modules_by_discipline(
    modules: Iterable[Module],
) -> dict[str, list[Module]]:
    """
    Group modules by primary discipline.

    Also detect cases where two different discipline names produce the
    same folder slug.
    """

    grouped: dict[str, list[Module]] = defaultdict(list)

    discipline_names_by_slug: dict[str, str] = {}

    for module in modules:
        for discipline in module.primary_disciplines:
            discipline_slug = slugify(discipline)

            existing_name = discipline_names_by_slug.get(
                discipline_slug
            )

            if (
                existing_name is not None
                and existing_name != discipline
            ):
                raise ValueError(
                    "Discipline slug collision: "
                    f"{existing_name!r} and {discipline!r} "
                    f"both become {discipline_slug!r}"
                )

            discipline_names_by_slug[
                discipline_slug
            ] = discipline

            grouped[discipline].append(module)

    return dict(grouped)


def relative_posix_path(
    target: Path,
    start: Path,
) -> str:
    """
    Return a relative path using forward slashes.

    This keeps generated MyST paths consistent on Windows, macOS,
    and Linux.
    """

    relative_path = os.path.relpath(
        target.resolve(),
        start.resolve(),
    )

    return Path(relative_path).as_posix()


def render_module_guide_page(
    module: Module,
    discipline: str,
    output_path: Path,
    ) -> list[str]:

    include_module_guide_path = relative_posix_path(
        module.guide_path,
        output_path.parent,
    )

    #JupyterLite URL for opening the activity notebook in the browser always directs to index.html
    #to open a specific notebook, the path to the notebook is appended to the URL as a query parameter
    JupyterURL = 'https://crossroads-ds.github.io/public-hub/jupyterlite/lab/index.html?path='

    return(
            [
                "```"
                f"{{include}} {include_module_guide_path}",
                "```",
                "",
                f"{{button}}`View Activity Guide </disciplines/{slugify(discipline)}/{module.slug}-activity.md>`",
                f"{{button}}`Open Activity in JupyterLite <{JupyterURL}{module.slug}/activity_interactive_python.ipynb>`",
                #f"{{button}}`Open Activity in Marimo <>`",
            ]
        )


def render_activity_guide_page(
    module: Module,
    discipline: str,
    output_path: Path,
    ) -> list[str]:

    include_activity_guide_path = relative_posix_path(
        (module.guide_path.parent / Path("activity_guide.md")),
        output_path.parent,
    )

    #JupyterLite URL for opening the activity notebook in the browser always directs to index.html
    #to open a specific notebook, the path to the notebook is appended to the URL as a query parameter
    JupyterURL = 'https://crossroads-ds.github.io/public-hub/jupyterlite/lab/index.html?path='

    return(
            [
                "```"
                f"{{include}} {include_activity_guide_path}",
                "```",
                "",
                f"{{button}}`Back to Module Guide </disciplines/{slugify(discipline)}/{module.slug}.md>`",
                f"{{button}}`Open Activity in JupyterLite <{JupyterURL}{module.slug}/activity_interactive_python.ipynb>`",
                #f"{{button}}`Open Activity in Marimo <>`",
            ]
        )


def render_page(
    module: Module,
    discipline: str,
    output_path: Path,
) -> str:
    """
    Generate the contents of one discipline-specific module page.

    When module_guide.md exists, it is included in the generated page.
    Otherwise, the script creates a small fallback page.

    If {discipline}.md exists, it is included before the module guide
    for that discipline's module page.
    """

    frontmatter = yaml.safe_dump(
        {
            "title": module.title,
        },
        sort_keys=False,
        allow_unicode=True,
    ).strip()

    parts = [
        "---",
        frontmatter,
        "---",
        "",
        "<!-- AUTO-GENERATED: DO NOT EDIT THIS FILE DIRECTLY. -->",
        "",
    ]

    
    if output_path.name == f"{module.slug}.md":
        filename = f"module_guide.md"
    else:
        filename = f"activity_guide.md"


    if not (module.guide_path.parent / Path(filename)).is_file():
        #desired file does not exist, generate a fallback page, this should not happen if the module is valid
        parts.extend(
                    [
                        f"# {module.title}",
                        "",
                        f"**Primary discipline:** {discipline}",
                        "",
                        (
                            "_The file could not be included because "
                            f"`{(module.guide_path.parent / Path(output_path.name))}` was not found._"
                        ),
                        "",
                    ]
                )
    else:
        #desired file exists
        if filename == f"module_guide.md":
            
            #check for existence of discipline blurb file, this file is optionally defined per discipline by the module author
            #searches for file:
            #public-modules/<module_slug>/<discipline>.md
            discipline_blurb = module.guide_path.parent / Path(f'{slugify(discipline)}.md')
            if discipline_blurb.is_file():
                include_discipline_path = relative_posix_path(
                    discipline_blurb,
                    output_path.parent,
                )
                parts.extend(
                            [
                                "```"
                                f"{{include}} {include_discipline_path}",
                                "```",
                            ]
                        )

            parts.extend(render_module_guide_page(
                            module=module,
                            discipline=discipline,
                            output_path=output_path,
                        )
            )
        
        elif filename == f"activity_guide.md":
            parts.extend(render_activity_guide_page(
                            module=module,
                            discipline=discipline,
                            output_path=output_path,
                        )
            )

    return "\n".join(parts)


def create_discipline_pages(
    modules: Iterable[Module],
    disciplines_directory: str | Path = "disciplines",
    *,
    clean: bool = False,
) -> list[Path]:
    """
    Create discipline folders and module Markdown pages.

    Example:

        disciplines/
        ├── computer-science/
        │   └── ocean-science-research-analysis.md
        └── data-science/
            └── ocean-science-research-analysis.md

    When clean=True, the existing disciplines directory is deleted before
    regeneration.

    Returns all generated page paths.
    """

    module_list = list(modules)
    root = Path(disciplines_directory)

    if clean and root.exists():
        shutil.rmtree(root)

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    grouped = group_modules_by_discipline(module_list)

    generated_pages: list[Path] = []

    for discipline in sorted(
        grouped,
        key=str.casefold,
    ):
        discipline_directory = (
            root / slugify(discipline)
        )

        discipline_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        discipline_modules = sorted(
            grouped[discipline],
            key=lambda module: (
                module.title.casefold(),
                module.slug,
            ),
        )

        for module in discipline_modules:
            
            module_path = (
                discipline_directory
                / f"{module.slug}.md"
            )
            activity_path = (
                discipline_directory
                / f"{module.slug}-activity.md"
            )

            for output_path in [module_path, activity_path]:
                content = render_page(
                    module=module,
                    discipline=discipline,
                    output_path=output_path,
                )

                # Avoid changing the file timestamp when the generated
                # content has not changed.
                if output_path.is_file():
                    existing_content = output_path.read_text(
                        encoding="utf-8"
                    )

                    if existing_content == content:
                        generated_pages.append(output_path)
                        continue

                output_path.write_text(
                    content,
                    encoding="utf-8",
                )

                generated_pages.append(output_path)

    return generated_pages


def build_myst_toc(
    modules: Iterable[Module],
    *,
    project_root: str | Path = ".",
    disciplines_directory: str | Path = "disciplines",
    index_file: str | Path = "index.md",
) -> list[dict[str, Any]]:
    """
    Build the hierarchical MyST project.toc structure.

    Result:

        toc:
          - file: index.md

          - title: Computer Science
            children:
              - file: disciplines/computer-science/module_guide.md
                title: Example Module
              - file: disciplines/computer-science/activity_guide.md
                hidden: True
    """

    module_list = list(modules)
    root = Path(project_root).resolve()

    disciplines_root = Path(disciplines_directory)

    if not disciplines_root.is_absolute():
        disciplines_root = root / disciplines_root

    index_path = Path(index_file)

    if not index_path.is_absolute():
        index_path = root / index_path

    toc: list[dict[str, Any]] = [
        {
            "file": relative_posix_path(
                index_path,
                root,
            ),
        }
    ]

    grouped = group_modules_by_discipline(module_list)

    for discipline in sorted(
        grouped,
        key=str.casefold,
    ):
        discipline_slug = slugify(discipline)

        children: list[dict[str, str]] = []

        discipline_modules = sorted(
            grouped[discipline],
            key=lambda module: (
                module.title.casefold(),
                module.slug,
            ),
        )

        for module in discipline_modules:
            page_path = (
                disciplines_root
                / discipline_slug
                / f"{module.slug}.md"
            )
            activity_path = (
                disciplines_root
                / discipline_slug
                / f"{module.slug}-activity.md"
            )

            children.append(
                {
                    "file": relative_posix_path(
                        page_path,
                        root,
                    ),
                    "title": module.title,
                } 
            )
            children.append(
                {
                    "file": relative_posix_path(
                        activity_path,
                        root,
                    ),
                    "hidden": True,
                }
            )

        toc.append(
            {
                "title": discipline,
                "children": children,
            }
        )

    return toc


def ensure_list_contains(
    values: list[Any],
    required_values: Iterable[Any],
) -> None:
    """
    Append required values that are not already in a list.
    """

    for required_value in required_values:
        if required_value not in values:
            values.append(required_value)


def write_myst_config(
    modules: Iterable[Module],
    myst_config_path: str | Path = "toc.yml",
    *,
    disciplines_directory: str | Path = "disciplines",
    index_file: str | Path = "index.md",
) -> Path:
    """
    Create or update toc.yml.

    Existing project and site settings are retained. The project.toc field
    is regenerated from the loaded modules.
    """

    config_path = Path(myst_config_path)
    project_root = config_path.parent.resolve()

    if config_path.is_file():
        try:
            loaded = load_yaml(config_path)

        except (OSError, yaml.YAMLError) as error:
            raise RuntimeError(
                f"Could not read {config_path}: {error}"
            ) from error

        if loaded is None:
            config: dict[str, Any] = {}

        elif isinstance(loaded, dict):
            config = loaded

        else:
            raise ValueError(
                f"{config_path} must contain a YAML mapping"
            )

    else:
        config = {}

    config.setdefault("version", 1)

    # Project configuration
    project = config.get("project")

    if not isinstance(project, dict):
        project = {}
        config["project"] = project

    # project.setdefault(
    #     "title",
    #     "Data Science CROSSROADS",
    # )

    # project.setdefault(
    #     "description",
    #     "Interactive data science learning modules.",
    # )

    # jupyter = project.get("jupyter")

    # if not isinstance(jupyter, dict):
    #     jupyter = {}
    #     project["jupyter"] = jupyter

    # jupyter.setdefault("lite", True)

    # exclude = project.get("exclude")

    # if not isinstance(exclude, list):
    #     exclude = []
    #     project["exclude"] = exclude

    # ensure_list_contains(
    #     exclude,
    #     DEFAULT_EXCLUDES,
    # )

    # Replace only the generated TOC.
    project["toc"] = build_myst_toc(
        modules,
        project_root=project_root,
        disciplines_directory=disciplines_directory,
        index_file=index_file,
    )

    # Site configuration
    # site = config.get("site")

    # if not isinstance(site, dict):
    #     site = {}
    #     config["site"] = site

    # site.setdefault(
    #     "template",
    #     "book-theme",
    # )

    # site.setdefault(
    #     "title",
    #     "Data Science CROSSROADS",
    # )

    # options = site.get("options")

    # if not isinstance(options, dict):
    #     options = {}
    #     site["options"] = options

    # options.setdefault("folders", True)
    # options.setdefault("logo", "logo.png")

    # options.setdefault(
    #     "logo_text",
    #     "Data Science: CROSSROADS",
    # )

    # config_path.parent.mkdir(
    #     parents=True,
    #     exist_ok=True,
    # )

    config_path.write_text(
        yaml.safe_dump(
            config,
            sort_keys=False,
            allow_unicode=True,
            width=1000,
        ),
        encoding="utf-8",
    )

    return config_path


def generate_catalog(
    *,
    project_root: str | Path = ".",
    public_modules_directory: str | Path = "public-modules",
    disciplines_directory: str | Path = "disciplines",
    myst_config: str | Path = "toc.yml",
    index_file: str | Path = "index.md",
    clean: bool = False,
    verbose: bool = True,
) -> tuple[list[Module], list[Path], Path]:
    """
    Run the complete generation process.

    This function can be imported and called from another Python script.
    """

    root = Path(project_root).resolve()

    public_modules_path = Path(
        public_modules_directory
    )

    if not public_modules_path.is_absolute():
        public_modules_path = (
            root / public_modules_path
        )

    disciplines_path = Path(
        disciplines_directory
    )

    if not disciplines_path.is_absolute():
        disciplines_path = (
            root / disciplines_path
        )

    myst_config_path = Path(myst_config)

    if not myst_config_path.is_absolute():
        myst_config_path = (
            root / myst_config_path
        )

    index_path = Path(index_file)

    if not index_path.is_absolute():
        index_path = root / index_path

    modules = load_modules(
        public_modules_path,
        verbose=verbose,
    )

    generated_pages = create_discipline_pages(
        modules,
        disciplines_path,
        clean=clean,
    )

    written_config = write_myst_config(
        modules,
        myst_config_path,
        disciplines_directory=disciplines_path,
        index_file=index_path,
    )

    return (
        modules,
        generated_pages,
        written_config,
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate discipline pages and a hierarchical "
            "MyST table of contents from public module metadata."
        )
    )

    parser.add_argument(
        "--project-root",
        default=".",
        help=(
            "Project root directory. "
            "Defaults to the current directory."
        ),
    )

    parser.add_argument(
        "--public-modules",
        default="public-modules",
        help=(
            "Directory containing the individual module folders."
        ),
    )

    parser.add_argument(
        "--disciplines",
        default="disciplines",
        help=(
            "Output directory for generated discipline pages."
        ),
    )

    parser.add_argument(
        "--myst-config",
        default="toc.yml",
        help=(
            "MyST configuration file to create or update."
        ),
    )

    parser.add_argument(
        "--index",
        default="index.md",
        help=(
            "Root page to place first in the MyST TOC."
        ),
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help=(
            "Delete the disciplines directory before regeneration."
        ),
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Do not print warnings for omitted modules."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    try:
        modules, generated_pages, config_path = generate_catalog(
            project_root=args.project_root,
            public_modules_directory=args.public_modules,
            disciplines_directory=args.disciplines,
            myst_config=args.myst_config,
            index_file=args.index,
            clean=args.clean,
            verbose=not args.quiet,
        )

    except (
        OSError,
        ValueError,
        RuntimeError,
    ) as error:
        print(
            f"Error: {error}",
            file=sys.stderr,
        )

        return 1

    print(
        f"Loaded {len(modules)} valid module(s)."
    )

    print(
        f"Generated {len(generated_pages)} "
        "discipline page(s)."
    )

    print(
        f"Updated MyST configuration: {config_path}"
    )

    for module in modules:
        print(f"  {module}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())