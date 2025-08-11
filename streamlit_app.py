import ast
import pandas as pd
from typing import List, Dict, Union
import re
import socket
import dns.resolver

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


def extract_domain_from_url(url: str) -> str:
    """Extract domain from various URL formats and company names."""
    if not url or pd.isna(url):
        return ""
    
    url = str(url).strip()
    
    # Handle LinkedIn company URLs
    if 'linkedin.com/company' in url:
        return 'linkedin.com'
    
    # Extract domain from regular URLs
    domain_pattern = r'https?://(?:www\.)?([^/\s]+)'
    match = re.search(domain_pattern, url)
    if match:
        return match.group(1)
    
    # If it's already a domain (no http/https)
    if '.' in url and not url.startswith('http'):
        return url
    
    # Handle company names that might be domains
    # Clean up company names and check if they could be domains
    cleaned = re.sub(r'[^\w\s.-]', '', url)  # Remove special chars except dots and hyphens
    cleaned = re.sub(r'\s+', '', cleaned)     # Remove spaces
    
    # If it looks like a domain (has dots and reasonable length)
    if '.' in cleaned and len(cleaned) > 5 and not cleaned.startswith('.'):
        return cleaned.lower()
    
    return ""

def find_best_company_domain(company_name: str) -> str:
    """Find the most likely domain for a company by checking multiple variations."""
    if not company_name or pd.isna(company_name):
        return ""
    
    company = str(company_name).strip()
    
    # Clean company name
    cleaned = re.sub(r'[^\w\s]', ' ', company)  # Keep only alphanumeric and spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()  # Normalize spaces
    
    if not cleaned:
        return ""
    
    # Split into words
    words = cleaned.split()
    
    # Priority order for domain extensions (most common first)
    extensions = ['.com', '.org', '.net', '.co', '.io', '.tech', '.app']
    
    # Generate domain candidates in priority order
    candidates = []
    
    if len(words) == 1:
        # Single word company
        word = words[0].lower()
        if len(word) > 2:
            for ext in extensions:
                candidates.append(f"{word}{ext}")
    
    elif len(words) == 2:
        # Two word company
        word1, word2 = words[0].lower(), words[1].lower()
        if len(word1) > 2 and len(word2) > 2:
            # Combined words
            for ext in extensions:
                candidates.append(f"{word1}{word2}{ext}")
            # Dotted format
            for ext in extensions:
                candidates.append(f"{word1}.{word2}{ext}")
    
    elif len(words) > 2:
        # Multi-word company - try first two words
        word1, word2 = words[0].lower(), words[1].lower()
        if len(word1) > 2 and len(word2) > 2:
            for ext in extensions:
                candidates.append(f"{word1}{word2}{ext}")
        
        # Try acronym
        acronym = ''.join([word[0].upper() for word in words if len(word) > 1])
        if len(acronym) > 2:
            for ext in extensions:
                candidates.append(f"{acronym.lower()}{ext}")
    
    # Return the first candidate (most common format)
    return candidates[0] if candidates else ""

def check_domain_mx(domain: str) -> bool:
    """Check if a domain has valid MX records (mail servers)."""
    try:
        # Try to resolve MX records
        dns.resolver.resolve(domain, 'MX')
        return True
    except Exception:
        return False

def check_domain_exists(domain: str) -> bool:
    """Check if a domain exists by trying to resolve it."""
    try:
        socket.gethostbyname(domain)
        return True
    except Exception:
        return False

def validate_domain_before_processing(domain: str) -> Dict[str, bool]:
    """Validate a domain before processing to avoid unnecessary SMTP attempts."""
    results = {
        "domain_exists": False,
        "has_mx_records": False,
        "is_valid": False
    }
    
    if not domain or domain == "linkedin.com":
        return results
    
    # Check if domain exists
    results["domain_exists"] = check_domain_exists(domain)
    
    if results["domain_exists"]:
        # Check if it has mail servers
        results["has_mx_records"] = check_domain_mx(domain)
        results["is_valid"] = results["has_mx_records"]
    
    return results

def process_csv_data(df: pd.DataFrame, name_columns: List[str], company_url_column: str) -> List[Dict[str, Union[str, List[str]]]]:
    """Process CSV data and extract domains and names for email finding."""
    email_data = []
    
    for idx, row in df.iterrows():
        # Extract names
        names = []
        for col in name_columns:
            if col in df.columns and pd.notna(row[col]) and str(row[col]).strip():
                name = str(row[col]).strip()
                if name and name not in names:  # Avoid duplicates
                    names.append(name)
        
        if not names:
            continue
            
        # Extract domain from company column
        company_value = row.get(company_url_column, "")
        domain = extract_domain_from_url(company_value)
        
        # Debug info
        print(f"Row {idx}: Company='{company_value}' -> Domain='{domain}'")
        
        # If no domain found, try to find the best company domain
        if not domain and company_value and not pd.isna(company_value):
            domain = find_best_company_domain(company_value)
            if domain:
                print(f"  Found best domain '{domain}' for company '{company_value}'")
        
        # If still no domain, try to extract from other columns that might contain URLs
        if not domain:
            for col in df.columns:
                if 'href' in col.lower() or 'url' in col.lower() or 'link' in col.lower():
                    potential_url = row.get(col, "")
                    if potential_url and pd.notna(potential_url):
                        domain = extract_domain_from_url(potential_url)
                        if domain:
                            print(f"  Found domain '{domain}' in column '{col}'")
                            break
        
        if not domain:
            continue
            
        # Validate domain before adding to processing list
        domain_validation = validate_domain_before_processing(domain)
        
        email_data.append({
            "domain": domain,
            "names": names,
            "original_row": row.to_dict(),
            "domain_validation": domain_validation
        })
    
    return email_data


def main() -> None:
    st.set_page_config(page_title="MailScout", page_icon="📧", layout="centered")
    st.title("📧 MailScout")
    st.caption(
        "Find potential business email addresses and validate via SMTP. Note: outbound port 25 must be open for validation."
    )

    options = render_sidebar()

    tab_find, tab_bulk, tab_csv, tab_dns, tab_utils = st.tabs(["Find emails", "Bulk", "CSV Upload", "DNS Only", "Utilities"])

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

    with tab_csv:
        st.subheader("CSV File Processing")
        st.caption("Upload a CSV file with names and company information to automatically find emails.")
        
        uploaded_file = st.file_uploader("Choose a CSV file", type=['csv'])
        
        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file)
                st.success(f"CSV loaded successfully! Shape: {df.shape}")
                
                # Show column selection
                st.subheader("Configure Column Mapping")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.write("**Available columns:**")
                    st.write(list(df.columns))
                
                with col2:
                    st.write("**Sample data:**")
                    st.dataframe(df.head(3))
                
                # Column selection
                st.subheader("Select Columns")
                name_columns = st.multiselect(
                    "Select name columns (e.g., First Name, Last Name)",
                    df.columns,
                    help="Choose columns containing person names"
                )
                
                company_url_column = st.selectbox(
                    "Select company/URL column",
                    [""] + list(df.columns),
                    help="Choose column containing company URLs or domains"
                )
                
                if name_columns and company_url_column:
                    st.success("Column mapping configured!")
                    
                    # Process CSV data
                    email_data = process_csv_data(df, name_columns, company_url_column)
                    
                    if email_data:
                        st.info(f"Found {len(email_data)} valid entries to process")
                        
                        # Show preview with more details
                        preview_data = []
                        for item in email_data[:10]:
                            preview_data.append({
                                "Names": " + ".join(item["names"]),
                                "Domain": item["domain"],
                                "Company Value": str(item["original_row"].get(company_url_column, ""))[:50] + "...",
                                "Sample Row": str(item["original_row"])[:100] + "..."
                            })
                        
                        preview_df = pd.DataFrame(preview_data)
                        st.write("**Preview of data to process:**")
                        st.dataframe(preview_df)
                        
                        # Show all extracted data for processing
                        st.write("**All extracted data for processing:**")
                        all_data = []
                        for item in email_data:
                            validation = item.get("domain_validation", {})
                            all_data.append({
                                "Names": " + ".join(item["names"]),
                                "Domain": item["domain"],
                                "Company": str(item["original_row"].get(company_url_column, ""))[:50],
                                "Domain Exists": "✅" if validation.get("domain_exists", False) else "❌",
                                "Has Mail Servers": "✅" if validation.get("has_mx_records", False) else "❌",
                                "Valid for Email": "✅" if validation.get("is_valid", False) else "❌"
                            })
                        st.dataframe(pd.DataFrame(all_data))
                        
                        # Filter to only valid domains
                        valid_domains = [item for item in email_data if item.get("domain_validation", {}).get("is_valid", False)]
                        invalid_domains = [item for item in email_data if not item.get("domain_validation", {}).get("is_valid", False)]
                        
                        if valid_domains:
                            st.success(f"✅ {len(valid_domains)} domains are valid and ready for email finding!")
                            st.info(f"❌ {len(invalid_domains)} domains are invalid and will be skipped.")
                        else:
                            st.warning("❌ No valid domains found. All generated domains appear to be invalid.")
                            st.info("This could mean:")
                            st.info("- The company names don't match their actual domains")
                            st.info("- The companies use different domain naming conventions")
                            st.info("- Some companies might not have websites")
                        
                        # Show potential domains for each company
                        st.write("**Domain validation results:**")
                        domain_analysis = []
                        for item in email_data:
                            company_name = str(item["original_row"].get(company_url_column, ""))
                            validation = item.get("domain_validation", {})
                            if company_name and not pd.isna(company_name):
                                best_domain = find_best_company_domain(company_name)
                                domain_analysis.append({
                                    "Company": company_name[:50],
                                    "Generated Domain": item["domain"],
                                    "Best Domain Found": best_domain if best_domain else "None",
                                    "Domain Exists": "✅" if validation.get("domain_exists", False) else "❌",
                                    "Has Mail Servers": "✅" if validation.get("has_mx_records", False) else "❌",
                                    "Valid for Email": "✅" if validation.get("is_valid", False) else "❌"
                                })
                        
                        if domain_analysis:
                            st.dataframe(pd.DataFrame(domain_analysis))
                        
                        # Run email finding only for valid domains
                        if valid_domains and st.button("🚀 Find Emails from CSV (Valid Domains Only)", type="primary"):
                            st.info("🔍 **DNS-Only Mode: Generating Email Candidates**")
                            st.write("Since SMTP validation is blocked, we'll generate email candidates for valid domains.")
                            
                            with st.spinner("Generating email candidates for valid domains..."):
                                try:
                                    all_candidates = []
                                    for item in valid_domains:
                                        domain = item["domain"]
                                        names = item["names"]
                                        if names and domain:
                                            # Generate email variants without SMTP validation
                                            scout = create_scout(options)
                                            try:
                                                candidates = scout.generate_email_variants(names, domain, normalize=options["normalize"])
                                                for email in candidates:
                                                    all_candidates.append({
                                                        "Domain": domain,
                                                        "Names": " + ".join(names),
                                                        "Generated Email": email,
                                                        "Status": "✅ Valid Domain + Generated",
                                                        "Validation": "DNS Only (SMTP Blocked)"
                                                    })
                                            except Exception:
                                                # Fallback to simple generation
                                                for name in names:
                                                    all_candidates.append({
                                                        "Domain": domain,
                                                        "Names": name,
                                                        "Generated Email": f"{name.lower()}@{domain}",
                                                        "Status": "✅ Valid Domain + Generated",
                                                        "Validation": "DNS Only (SMTP Blocked)"
                                                    })
                                    
                                    if all_candidates:
                                        results_df = pd.DataFrame(all_candidates)
                                        st.success(f"✅ Generated {len(all_candidates)} email candidates for valid domains!")
                                        st.dataframe(results_df)
                                        
                                        # Download button
                                        csv = results_df.to_csv(index=False)
                                        st.download_button(
                                            label="📥 Download Valid Domain Results CSV",
                                            data=csv,
                                            file_name="valid_domain_emails.csv",
                                            mime="text/csv"
                                        )
                                        
                                        st.info("💡 **Next Steps:**")
                                        st.info("- These emails are generated from valid domains")
                                        st.info("- Use a separate email validation service to verify them")
                                        st.info("- Or try sending test emails to see which ones work")
                                    else:
                                        st.warning("Could not generate email candidates for valid domains.")
                                        
                                except Exception as exc:
                                    st.error(f"Error generating email candidates: {exc}")
                                    st.exception(exc)
                        elif not valid_domains:
                            st.info("💡 **Alternative: Generate Email Candidates**")
                            st.write("Even without valid domains, you can generate email candidates for manual verification:")
                            
                            if st.button("📧 Generate Email Candidates", type="secondary"):
                                all_candidates = []
                                for item in email_data:
                                    domain = item["domain"]
                                    names = item["names"]
                                    if names and domain:
                                        # Generate email variants
                                        scout = create_scout(options)
                                        try:
                                            candidates = scout.generate_email_variants(names, domain, normalize=options["normalize"])
                                            for email in candidates:
                                                all_candidates.append({
                                                    "Domain": domain,
                                                    "Names": " + ".join(names),
                                                    "Generated Email": email,
                                                    "Status": "Generated (Not Validated)",
                                                    "Domain Status": "❌ Invalid Domain"
                                                })
                                        except Exception:
                                            # Fallback to simple generation
                                            for name in names:
                                                all_candidates.append({
                                                    "Domain": domain,
                                                    "Names": name,
                                                    "Generated Email": f"{name.lower()}@{domain}",
                                                    "Status": "Generated (Not Validated)",
                                                    "Domain Status": "❌ Invalid Domain"
                                                })
                                
                                if all_candidates:
                                    candidates_df = pd.DataFrame(all_candidates)
                                    st.success(f"Generated {len(all_candidates)} email candidates!")
                                    st.dataframe(candidates_df)
                                    
                                    # Download candidates
                                    csv = candidates_df.to_csv(index=False)
                                    st.download_button(
                                        label="📥 Download Email Candidates CSV",
                                        data=csv,
                                        file_name="email_candidates.csv",
                                        mime="text/csv"
                                    )
                                else:
                                    st.warning("Could not generate email candidates.")
                    else:
                        st.warning("No valid data found. Check your column selection and data format.")
                        st.info("Debug info:")
                        st.write(f"Selected name columns: {name_columns}")
                        st.write(f"Selected company column: {company_url_column}")
                        st.write(f"Sample company values:")
                        sample_values = df[company_url_column].dropna().head(5).tolist()
                        for val in sample_values:
                            st.write(f"- '{val}' -> Domain: '{extract_domain_from_url(str(val))}'")
                        
            except Exception as e:
                st.error(f"Error reading CSV file: {e}")

    with tab_dns:
        st.subheader("🔍 DNS-Only Email Generation")
        st.caption("Generate email candidates without SMTP validation. Perfect when port 25 is blocked.")
        
        domain_dns = st.text_input("Domain", placeholder="example.com", key="dns_domain")
        names_dns = st.text_area("Names (optional)", height=120, help="Enter names separated by commas or new lines", key="dns_names")
        
        col1, col2 = st.columns(2)
        with col1:
            check_domain = st.button("🔍 Check Domain", type="secondary")
        with col2:
            generate_emails = st.button("📧 Generate Emails", type="primary")
        
        parsed_names_dns = parse_names_input(names_dns) if names_dns else None
        
        if check_domain and domain_dns:
            with st.spinner("Checking domain validity..."):
                validation = validate_domain_before_processing(domain_dns)
                
                st.write("**Domain Validation Results:**")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Domain Exists", "✅ Yes" if validation["domain_exists"] else "❌ No")
                with col2:
                    st.metric("Has Mail Servers", "✅ Yes" if validation["has_mx_records"] else "❌ No")
                with col3:
                    st.metric("Valid for Email", "✅ Yes" if validation["is_valid"] else "❌ No")
                
                if validation["is_valid"]:
                    st.success(f"✅ {domain_dns} is a valid domain with mail servers!")
                elif validation["domain_exists"]:
                    st.warning(f"⚠️ {domain_dns} exists but has no mail servers.")
                else:
                    st.error(f"❌ {domain_dns} does not exist.")
        
        if generate_emails and domain_dns:
            with st.spinner("Generating email candidates..."):
                try:
                    scout = create_scout(options)
                    all_candidates = []
                    
                    if parsed_names_dns:
                        # Generate emails for specific names
                        if isinstance(parsed_names_dns[0], list):
                            for person in parsed_names_dns:
                                candidates = scout.generate_email_variants(person, domain_dns, normalize=options["normalize"])
                                for email in candidates:
                                    all_candidates.append({
                                        "Domain": domain_dns,
                                        "Names": " + ".join(person),
                                        "Generated Email": email,
                                        "Type": "Name-based"
                                    })
                        else:
                            candidates = scout.generate_email_variants(parsed_names_dns, domain_dns, normalize=options["normalize"])
                            for email in candidates:
                                all_candidates.append({
                                    "Domain": domain_dns,
                                    "Names": " + ".join(parsed_names_dns),
                                    "Generated Email": email,
                                    "Type": "Name-based"
                                })
                    else:
                        # Generate common prefixes
                        candidates = scout.generate_prefixes(domain_dns)
                        for email in candidates:
                            all_candidates.append({
                                "Domain": domain_dns,
                                "Names": "N/A",
                                "Generated Email": email,
                                "Type": "Common Prefix"
                            })
                    
                    if all_candidates:
                        results_df = pd.DataFrame(all_candidates)
                        st.success(f"✅ Generated {len(all_candidates)} email candidates!")
                        st.dataframe(results_df)
                        
                        # Download button
                        csv = results_df.to_csv(index=False)
                        st.download_button(
                            label="📥 Download Email Candidates CSV",
                            data=csv,
                            file_name=f"dns_emails_{domain_dns}.csv",
                            mime="text/csv"
                        )
                        
                        st.info("💡 **Next Steps:**")
                        st.info("- These are generated email candidates")
                        st.info("- Use an email validation service to verify them")
                        st.info("- Or try sending test emails to see which ones work")
                    else:
                        st.warning("Could not generate email candidates.")
                        
                except Exception as exc:
                    st.error(f"Error generating emails: {exc}")
                    st.exception(exc)

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


