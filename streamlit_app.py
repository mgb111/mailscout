import ast
from typing import List, Dict, Union

import streamlit as st

from mailscout import Scout


def parse_names_input(raw_names: str) -> Union[List[str], List[List[str]], None]:
    """
    Parse user-provided names input into supported formats:
    - Empty string -> None
    - Comma/space/newline separated single-person names -> List[str]
    - JSON-like list of lists -> List[List[str]]

    Examples accepted:
    - "John Doe" -> ["John", "Doe"]
    - "John, Doe" -> ["John", "Doe"]
    - "John Doe\nJane Smith" -> [["John", "Doe"], ["Jane", "Smith"]]
    - "[[\"John\", \"Doe\"], [\"Jane\", \"Smith\"]]" -> [["John","Doe"],["Jane","Smith"]]
    """
    cleaned = (raw_names or "").strip()
    if not cleaned:
        return None

    # Try to parse as Python literal (JSON-like) first
    try:
        parsed = ast.literal_eval(cleaned)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
            return parsed  # List[List[str]]
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed  # List[str]
    except Exception:
        pass

    # If multiple lines, treat each line as one person
    if "\n" in cleaned:
        people = []
        for line in cleaned.splitlines():
            line = line.strip()
            if not line:
                continue
            # Split on comma first; if not present, split on whitespace
            if "," in line:
                tokens = [t.strip() for t in line.split(",") if t.strip()]
            else:
                tokens = [t.strip() for t in line.split() if t.strip()]
            if tokens:
                people.append(tokens)
        return people if people else None

    # Single line -> List[str]
    if "," in cleaned:
        return [t.strip() for t in cleaned.split(",") if t.strip()]
    return [t.strip() for t in cleaned.split() if t.strip()]


def render_sidebar() -> Dict[str, Union[bool, int]]:
    st.sidebar.header("Settings")
    check_variants = st.sidebar.toggle("Generate name variants", value=True)
    check_prefixes = st.sidebar.toggle("Check common prefixes (no names)", value=True)
    check_catchall = st.sidebar.toggle("Check catch-all before searching", value=True)
    normalize = st.sidebar.toggle("Normalize names", value=True)
    smtp_timeout = st.sidebar.number_input("SMTP timeout (s)", min_value=1, max_value=30, value=2)
    num_threads = st.sidebar.slider("Worker threads", min_value=1, max_value=32, value=5)
    num_bulk_threads = st.sidebar.slider("Bulk worker threads", min_value=1, max_value=16, value=1)

    return {
        "check_variants": check_variants,
        "check_prefixes": check_prefixes,
        "check_catchall": check_catchall,
        "normalize": normalize,
        "smtp_timeout": int(smtp_timeout),
        "num_threads": int(num_threads),
        "num_bulk_threads": int(num_bulk_threads),
    }


def create_scout(options: Dict[str, Union[bool, int]]) -> Scout:
    return Scout(
        check_variants=options["check_variants"],
        check_prefixes=options["check_prefixes"],
        check_catchall=options["check_catchall"],
        normalize=options["normalize"],
        num_threads=options["num_threads"],
        num_bulk_threads=options["num_bulk_threads"],
        smtp_timeout=options["smtp_timeout"],
    )


def main() -> None:
    st.set_page_config(page_title="MailScout", page_icon="📧", layout="centered")
    st.title("📧 MailScout")
    st.caption(
        "Find potential business email addresses and validate via SMTP. Note: outbound port 25 must be open for validation."
    )

    options = render_sidebar()

    tab_find, tab_bulk, tab_utils = st.tabs(["Find emails", "Bulk", "Utilities"])

    with tab_find:
        st.subheader("Find Emails for a Domain")
        domain = st.text_input("Domain", placeholder="example.com")
        names_help = (
            "Enter names. Examples:\n"
            "- John Doe\n"
            "- John, Doe\n"
            "- One per line for multiple people (e.g., 'John Doe' then 'Jane Smith')\n"
            "- Or a JSON-like list e.g., [[\"John\",\"Doe\"],[\"Jane\",\"Smith\"]]"
        )
        raw_names = st.text_area("Names (optional)", height=120, help=names_help)

        col1, col2 = st.columns(2)
        with col1:
            run_find = st.button("Find valid emails", type="primary")
        with col2:
            show_generated = st.button("Preview generated candidates")

        parsed_names = parse_names_input(raw_names)

        if show_generated and domain:
            scout = create_scout(options)
            with st.spinner("Generating candidates..."):
                candidates: List[str] = []
                if parsed_names:
                    if isinstance(parsed_names[0], list):  # type: ignore[index]
                        for person in parsed_names:  # type: ignore[assignment]
                            candidates.extend(
                                scout.generate_email_variants(person, domain, normalize=options["normalize"])  # type: ignore[arg-type]
                            )
                    else:
                        candidates = scout.generate_email_variants(parsed_names, domain, normalize=options["normalize"])  # type: ignore[arg-type]

                if not parsed_names and options["check_prefixes"]:
                    candidates = scout.generate_prefixes(domain)

            if candidates:
                st.success(f"Generated {len(candidates)} candidates")
                st.dataframe(sorted(candidates))
            else:
                st.info("No candidates to show. Provide names or enable prefix generation.")

        if run_find and domain:
            scout = create_scout(options)
            with st.spinner("Checking deliverability via SMTP (may take time)..."):
                try:
                    valid_emails = scout.find_valid_emails(domain, parsed_names)
                except Exception as exc:
                    st.error(f"Error while finding emails: {exc}")
                    valid_emails = []

            if valid_emails:
                st.success(f"Found {len(valid_emails)} valid email(s)")
                st.dataframe(sorted(valid_emails))
            else:
                st.warning("No valid emails found. The domain may be catch-all or SMTP may be blocked.")

    with tab_bulk:
        st.subheader("Bulk Email Finding")
        st.caption("Provide JSON-like list: [{'domain': 'example.com', 'names': ['John', 'Doe']}, {...}]")
        raw_bulk = st.text_area(
            "Bulk data",
            height=160,
            placeholder="[{\"domain\": \"example.com\", \"names\": [\"John\", \"Doe\"]}]",
        )
        run_bulk = st.button("Run bulk search", type="secondary")

        if run_bulk:
            scout = create_scout(options)
            try:
                email_data: List[Dict[str, Union[str, List[str]]]] = ast.literal_eval(raw_bulk)
            except Exception:
                st.error("Could not parse bulk data. Please provide a valid Python/JSON-like list of dicts.")
                email_data = []

            if email_data:
                with st.spinner("Running bulk search..."):
                    try:
                        results = scout.find_valid_emails_bulk(email_data)
                    except Exception as exc:
                        st.error(f"Error during bulk search: {exc}")
                        results = []

                if results:
                    st.success(f"Completed {len(results)} task(s)")
                    st.json(results)
                else:
                    st.warning("No results produced.")

    with tab_utils:
        st.subheader("Utilities")
        st.caption("Quick helpers for SMTP and normalization.")
        util_choice = st.selectbox("Choose a utility", ["Check SMTP", "Check catch-all", "Normalize name"])  # noqa: E501

        if util_choice == "Check SMTP":
            email = st.text_input("Email", placeholder="user@example.com")
            if st.button("Check deliverability") and email:
                scout = create_scout(options)
                with st.spinner("Connecting to MX over SMTP..."):
                    result = scout.check_smtp(email)
                st.write("Deliverable:" if result else "Not deliverable:", email)

        elif util_choice == "Check catch-all":
            domain_util = st.text_input("Domain", placeholder="example.com")
            if st.button("Check catch-all") and domain_util:
                scout = create_scout(options)
                with st.spinner("Checking catch-all via SMTP..."):
                    result = scout.check_email_catchall(domain_util)
                st.write(f"Domain {domain_util} is catch-all: {bool(result)}")

        else:  # Normalize name
            name = st.text_input("Name", placeholder="Łukasz Nowak")
            if st.button("Normalize") and name:
                scout = create_scout(options)
                normalized = scout.normalize_name(name)
                st.code(normalized)


if __name__ == "__main__":
    main()


