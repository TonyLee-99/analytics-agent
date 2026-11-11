"""Author-written half of the v2 question set (60 items). Writes eval_sets/v2/questions_author.jsonl.

Written 2026-11-11 by the repo author from the corpus text and the schema, before the first scored run.
Gold SQL implements the governed definitions in semantic/chicago311.yaml directly on wh.* tables; the
harness runs it as the owner restricted to the asking user's departments (policy model, not RLS)."""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "eval_sets" / "v2" / "questions_author.jsonl"
DTC = "EXTRACT(EPOCH FROM (closed_at - created_at)) / 86400.0"
MED_C = f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {DTC})"
Y25 = "created_at >= TIMESTAMP '2025-01-01' AND created_at < TIMESTAMP '2026-01-01'"
SLA_RES = ("SELECT count(*) FILTER (WHERE {dtc} <= b.benchmark_days) * 1.0 / nullif(count(*), 0) FROM wh.service_request r "
           "JOIN wh.request_benchmark b ON b.sr_type = upper(r.sr_type) WHERE r.status = 'Completed' AND NOT r.is_duplicate "
           "AND b.benchmark_days > 0 AND {where}")
SLA_ALL = ("SELECT count(*) FILTER (WHERE r.status = 'Completed' AND {dtc} <= b.benchmark_days) * 1.0 / nullif(count(*), 0) "
           "FROM wh.service_request r JOIN wh.request_benchmark b ON b.sr_type = upper(r.sr_type) "
           "WHERE NOT r.is_duplicate AND b.benchmark_days > 0 AND {where}")
RDTC = "EXTRACT(EPOCH FROM (r.closed_at - r.created_at)) / 86400.0"

Q = []


def add(user, type_, question, docs=(), ref="", facts=(), sql=None):
    Q.append({"id": f"au-q{len(Q) + 1:02d}", "author": "author", "user": user, "type": type_, "question": question,
              "gold_doc_ids": list(docs), "reference_answer": ref, "key_facts": list(facts), "gold_sql": sql})


# ---------------- docs (20)
add("alex", "docs", "If I call 311 from my cell phone and can't get through, what number should I use?",
    ["web-depts-311-supp-info-faq"], "Call 312.744.5000 if a cell phone cannot get through to 311.", ["312.744.5000"])
add("jules", "docs", "When did Chicago launch its 311 system, and what did it replace?",
    ["web-depts-311-supp-info-311hist"], "The City implemented 311 in January 1999 as a one-stop center for all city services and "
    "non-emergency police services, eliminating a number of smaller call centers (replacing an outdated mainframe system).",
    ["January 1999", "one-stop", "smaller call centers"])
add("alex", "docs", "Roughly how many calls did the 311 Call Center receive last year, and how many languages can operators translate?",
    ["web-depts-311-provdrs-callops"], "About 2.8 million calls; translators provide translation for more than 100 languages.",
    ["2.8 million", "100 languages"])
add("jules", "docs", "How do residents start using ChiTEXT, and what options does its menu offer?",
    ["web-depts-311-supp-info-chitext"], "Text CHICAGO to short code 311311; the menu offers contact information, location search, "
    "towed/relocated vehicle, city alerts and request services.", ["CHICAGO", "311311", "towed"])
add("dev", "docs", "How does the Graffiti Removal Program remove graffiti from brick versus metal surfaces?",
    ["web-depts-streets-provdrs-graffiti-blasters"], "Blast trucks use baking soda under high water pressure on brick, stone and "
    "mineral surfaces; paint trucks cover graffiti on metal or wood.", ["baking soda", "paint trucks"])
add("rosa", "docs", "What is the city's tree planting goal under Our Roots Chicago, and how many parkway trees does Forestry maintain?",
    ["web-depts-streets-provdrs-forestry"], "Plant 75,000 new trees; more than 500,000 parkway trees are maintained by the DSS Bureau of Forestry.",
    ["75,000", "500,000"])
add("maria", "docs", "How many street and alley lights and signalized intersections does CDOT's Electrical Operations maintain?",
    ["web-depts-cdot-provdrs-traffic-signals-andstreetlights"], "Nearly 300,000 street and alley lights and more than 3,000 signalized "
    "intersections; all-block outages are prioritized before single lights.", ["300,000", "3,000"])
add("maria", "docs", "What is the target response time for pothole repairs according to CDOT's performance metric?",
    ["portal-sbt5-2ec8"], "The target response time for pothole repairs is within 7 days.", ["7 days"])
add("dev", "docs", "What is the target response time for rodent baiting requests in the Streets & Sanitation performance metric?",
    ["portal-ga2y-gwbs"], "Within 7 days.", ["7 days"])
add("dev", "docs", "What is the performance target for completing a graffiti removal request?",
    ["portal-qcfn-tiw7"], "10 days.", ["10 days"])
add("maria", "docs", "What counts as a street light 'All Out' and what is its target repair time?",
    ["portal-phrs-x2nm"], "An outage of 3 or more lights; the electrician checks the whole circuit; target is within 4 days.",
    ["3 or more", "4 days"])
add("jules", "docs", "Which service requests are excluded from the City's 311 median response time dataset?",
    ["portal-u6fz-87ei"], "Duplicates, requests created by City departments, and requests that did not come from a 311 phone call or "
    "the CHI 311 app, website or API.", ["duplicate", "created by City departments", "phone call"])
add("alex", "docs", "In the 311 service requests dataset, what does the OWNER_DEPARTMENT column mean compared with CREATED_DEPARTMENT?",
    ["portal-v6vf-nfxy"], "OWNER_DEPARTMENT is the department with initial responsibility; CREATED_DEPARTMENT is the department, if any, "
    "that created the request.", ["initial responsibility", "created the service request"])
add("priya", "docs", "How much drinking water does the Department of Water Management deliver each day and to whom?",
    ["web-depts-water-supp-info-consumer-confidencereports"], "Almost one billion gallons a day to Chicago and 120 suburbs.",
    ["one billion gallons", "120 suburbs"])
add("priya", "docs", "How long should residents flush their water after it has been stagnant, and why?",
    ["web-depts-water-supp-info-winter-weather-and-your-water-system"], "Flush for 5 minutes after six hours or more of stagnation; "
    "flushing pulls corrosion control through the pipes and helps keep contaminants such as lead out.",
    ["5 minutes", "six hours", "corrosion control"])
add("alex", "docs", "Where should 311 FOIA requests be emailed, and are FOIA requests public?",
    ["web-depts-311-supp-info-311-foia"], "Email 311foia@cityofchicago.org; all FOIA requests are posted online and are public information.",
    ["311foia@cityofchicago.org", "public"])
add("grace", "docs", "About how many food establishments are licensed in Chicago, and which division inspects them?",
    ["web-depts-cdph-provdrs-food-safety"], "Approximately 16,000; the CDPH Food Protection Division conducts periodic inspections.",
    ["16,000", "Food Protection"])
add("lena", "docs", "What hours does Chicago Animal Care and Control accept animal intake?",
    ["web-depts-cacc-supp-info-animal-intake0"], "Daily between 3pm and 7pm (intake may be suspended earlier).", ["3pm", "7pm"])
add("omar", "docs", "When does the City's budget process begin and how do residents give input?",
    ["web-depts-obm-provdrs-budget-svcs-budgetinformation"], "It begins in the spring with internal analyses; each summer the City "
    "hosts community roundtables and public forums whose feedback informs the Mayor's Budget Recommendations.",
    ["spring", "roundtables", "summer"])
add("dev", "docs", "How did the City reduce the backlog of abandoned vehicles waiting to be towed?",
    ["web-depts-streets-provdrs-traffic"], "In 1989 the City assigned towing of junk vehicles to private towing firms, cutting the backlog dramatically.",
    ["1989", "private towing"])

# ---------------- sql (20)
add("maria", "sql", "How many pothole complaints (Pothole in Street Complaint) were created in 2025?",
    sql=f"SELECT count(*) FROM wh.service_request WHERE sr_type = 'Pothole in Street Complaint' AND {Y25}")
add("dev", "sql", "How many rodent baiting requests were created each month in 2025, including duplicates?",
    sql=f"SELECT date_trunc('month', created_at)::date AS month, count(*) FROM wh.service_request "
        f"WHERE sr_type = 'Rodent Baiting/Rat Complaint' AND {Y25} GROUP BY 1 ORDER BY 1")
add("priya", "sql", "What are the top 5 request types for Water Management by number of requests created since 2025-01-01?",
    sql="SELECT sr_type, count(*) FROM wh.service_request WHERE created_at >= TIMESTAMP '2025-01-01' GROUP BY 1 "
        "ORDER BY 2 DESC LIMIT 5")
add("sam", "sql", "How many Buildings requests are still open (non-duplicate) right now?",
    sql="SELECT count(*) FROM wh.service_request WHERE is_open AND NOT is_duplicate")
add("lena", "sql", "Which 5 departments had the largest open backlog (non-duplicate open requests) at the last refresh?",
    sql="SELECT dept_code, count(*) FROM wh.service_request WHERE is_open AND NOT is_duplicate GROUP BY 1 ORDER BY 2 DESC LIMIT 5")
add("dev", "sql", "What share of graffiti removal requests created in 2025 were flagged as duplicates?",
    sql=f"SELECT count(*) FILTER (WHERE is_duplicate) * 1.0 / count(*) FROM wh.service_request "
        f"WHERE sr_type = 'Graffiti Removal Request' AND {Y25}")
add("maria", "sql", "What was the cancellation rate of non-duplicate CDOT requests created in 2025?",
    sql=f"SELECT count(*) FILTER (WHERE is_canceled) * 1.0 / count(*) FROM wh.service_request WHERE NOT is_duplicate AND {Y25}")
add("jules", "sql", "How many 311 INFORMATION ONLY CALL requests were created in October 2026?",
    sql="SELECT count(*) FROM wh.service_request WHERE sr_type = '311 INFORMATION ONLY CALL' AND created_at >= TIMESTAMP "
        "'2026-10-01' AND created_at < TIMESTAMP '2026-11-01'")
add("lena", "sql", "Which 5 community areas had the most requests created in 2025?",
    sql=f"SELECT ca.community_area_name, count(*) FROM wh.service_request r JOIN wh.community_area ca ON ca.community_area = "
        f"r.community_area WHERE r.{Y25.replace('created_at <', 'r.created_at <')} GROUP BY 1 ORDER BY 2 DESC LIMIT 5")
add("nina", "sql", "How many work requests (excluding duplicates) did CDOT and Streets and Sanitation each receive in 2025?",
    sql=f"SELECT dept_code, count(*) FROM wh.service_request WHERE NOT is_duplicate AND {Y25} AND dept_code IN ('CDOT', 'DSS') "
        f"GROUP BY 1 ORDER BY 1")
add("maria", "sql", "How many Street Light Out Complaint requests were created per quarter in 2025?",
    sql=f"SELECT date_trunc('quarter', created_at)::date, count(*) FROM wh.service_request WHERE sr_type = 'Street Light Out "
        f"Complaint' AND {Y25} GROUP BY 1 ORDER BY 1")
add("dev", "sql", "What was the median days to close for completed Graffiti Removal Requests created in 2025, counting completed requests only?",
    sql=f"SELECT {MED_C} FROM wh.service_request WHERE status = 'Completed' AND closed_at IS NOT NULL AND "
        f"sr_type = 'Graffiti Removal Request' AND {Y25}")
add("priya", "sql", "How many Water in Basement Complaint requests were created in 2025?",
    sql=f"SELECT count(*) FROM wh.service_request WHERE sr_type = 'Water in Basement Complaint' AND {Y25}")
add("lena", "sql", "Which wards had the 3 highest numbers of requests created in 2025?",
    sql=f"SELECT ward, count(*) FROM wh.service_request WHERE ward IS NOT NULL AND {Y25} GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
add("grace", "sql", "How many Public Health requests were created in 2025, and what share were duplicates?",
    sql=f"SELECT count(*), count(*) FILTER (WHERE is_duplicate) * 1.0 / count(*) FROM wh.service_request WHERE {Y25}")
add("jules", "sql", "What were the top 3 origins (how requests were opened) for requests created in 2025?",
    sql=f"SELECT origin, count(*) FROM wh.service_request WHERE {Y25} GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
add("rosa", "sql", "How many Tree Emergency requests were created in 2025?",
    sql=f"SELECT count(*) FROM wh.service_request WHERE sr_type = 'Tree Emergency' AND {Y25}")
add("lena", "sql", "What were the requests per 1,000 residents in the Loop and Austin community areas in 2025?",
    sql=f"SELECT ca.community_area_name, count(*) * 1000.0 / max(ca.population_acs) FROM wh.service_request r JOIN "
        f"wh.community_area ca ON ca.community_area = r.community_area WHERE ca.community_area_name IN ('Loop', 'Austin') "
        f"AND r.created_at >= TIMESTAMP '2025-01-01' AND r.created_at < TIMESTAMP '2026-01-01' GROUP BY 1 ORDER BY 1")
add("sam", "sql", "How many Building Violation requests were created in the first half of 2026 (January through June)?",
    sql="SELECT count(*) FROM wh.service_request WHERE sr_type = 'Building Violation' AND created_at >= TIMESTAMP '2026-01-01' "
        "AND created_at < TIMESTAMP '2026-07-01'")
add("alex", "sql", "How many 311 City Services requests were created in 2025 in total, duplicates included?",
    sql=f"SELECT count(*) FROM wh.service_request WHERE {Y25}")

# ---------------- mixed (12)
add("maria", "mixed", "What is CDOT's stated target for pothole repairs, and what was the actual median days to close for completed "
    "pothole complaints created in 2025 (completed only)?", ["portal-sbt5-2ec8"], "Target is within 7 days; the median is from the warehouse.",
    ["7 days"], f"SELECT {MED_C} FROM wh.service_request WHERE status = 'Completed' AND closed_at IS NOT NULL AND "
                f"sr_type = 'Pothole in Street Complaint' AND {Y25}")
add("dev", "mixed", "What is the rodent baiting target response time, and what was the median days to close for completed rodent "
    "baiting requests created in 2025, counting completed requests only?", ["portal-ga2y-gwbs"], "Target within 7 days; median from data.",
    ["7 days"], f"SELECT {MED_C} FROM wh.service_request WHERE status = 'Completed' AND closed_at IS NOT NULL AND "
                f"sr_type = 'Rodent Baiting/Rat Complaint' AND {Y25}")
add("dev", "mixed", "How does the Graffiti Removal Program work, and how many graffiti removal requests were created in 2025?",
    ["web-depts-streets-provdrs-graffiti-blasters"], "Blast trucks with baking soda and paint trucks; count from data.",
    ["baking soda"], f"SELECT count(*) FROM wh.service_request WHERE sr_type = 'Graffiti Removal Request' AND {Y25}")
add("priya", "mixed", "Does the City offer free water lead testing, and how many Water Lead Test Kit Requests were created in 2025?",
    ["web-depts-water-supp-info-winter-weather-and-your-water-system", "web-depts-water-supp-info-consumer-confidencereports"],
    "Yes, free residential water testing (test kit or a DWM visit); count from data.", ["free"],
    f"SELECT count(*) FROM wh.service_request WHERE sr_type = 'Water Lead Test Kit Request' AND {Y25}")
add("maria", "mixed", "What does CDOT count as a street light 'All Out', and how many Street Light Out Complaint requests were "
    "created in 2025?", ["portal-phrs-x2nm"], "An outage of 3 or more lights; count from data.", ["3 or more"],
    f"SELECT count(*) FROM wh.service_request WHERE sr_type = 'Street Light Out Complaint' AND {Y25}")
add("rosa", "mixed", "What does the Bureau of Forestry do, and how many Tree Removal Inspection requests were created in 2025?",
    ["web-depts-streets-provdrs-forestry"], "Plants and trims parkway trees, handles insect/disease problems and tree emergencies; count from data.",
    ["parkway"], f"SELECT count(*) FROM wh.service_request WHERE sr_type = 'Tree Removal Inspection' AND {Y25}")
add("jules", "mixed", "Which services does the 311 FAQ list as most requested, and how many Graffiti Removal Requests and Pothole in "
    "Street Complaints were created in 2025?", ["web-depts-311-supp-info-faq"], "Street Light Out, Garbage Cart, Rodent Baiting, Tree "
    "Trim, Graffiti Removal, Pothole in Street, ...; counts from data.", ["Street Light Out", "Rodent Baiting"],
    f"SELECT sr_type, count(*) FROM wh.service_request WHERE sr_type IN ('Graffiti Removal Request', 'Pothole in Street Complaint') "
    f"AND {Y25} GROUP BY 1 ORDER BY 1")
add("dev", "mixed", "How did the City change abandoned-vehicle towing, and how many Abandoned Vehicle Complaints are still open (non-duplicate)?",
    ["web-depts-streets-provdrs-traffic"], "Since 1989 private towing firms tow junk vehicles; open count from data.", ["private towing"],
    "SELECT count(*) FROM wh.service_request WHERE sr_type = 'Abandoned Vehicle Complaint' AND is_open AND NOT is_duplicate")
add("jules", "mixed", "Which requests does the City exclude from its published median response time, and what is the duplicate rate "
    "of all requests created in 2025?", ["portal-u6fz-87ei"], "Duplicates, City-created and non-311-channel requests; rate from data.",
    ["duplicate"], f"SELECT count(*) FILTER (WHERE is_duplicate) * 1.0 / count(*) FROM wh.service_request WHERE {Y25}")
add("sam", "mixed", "What does the Department of Buildings say about vacant building registration, and how many Building Violation "
    "requests were created in 2025?", ["web-depts-bldgs-provdrs-inspect"], "Owners of vacant buildings must register and maintain them; "
    "count from data.", ["vacant"], f"SELECT count(*) FROM wh.service_request WHERE sr_type = 'Building Violation' AND {Y25}")
add("alex", "mixed", "How can residents check the status of a 311 request, and how many 311 City Services requests were created in "
    "September 2026?", ["web-depts-311-supp-info-faq"], "Call 311 with the service request number or address (or check online); count from data.",
    ["service request number"], "SELECT count(*) FROM wh.service_request WHERE created_at >= TIMESTAMP '2026-09-01' AND created_at < "
    "TIMESTAMP '2026-10-01'")
add("grace", "mixed", "What does the CDPH Food Protection Division do, and how many Public Health requests are still open (non-duplicate)?",
    ["web-depts-cdph-provdrs-food-safety"], "Periodic inspections of about 16,000 licensed food establishments; open count from data.",
    ["inspections"], "SELECT count(*) FROM wh.service_request WHERE is_open AND NOT is_duplicate")

# ---------------- metric definitions (8): 4 ambiguous -> clarify, 4 explicit -> sql
add("maria", "clarify", "What's the median time to close for CDOT requests in 2025?",
    ref="Two definitions exist: completed-only and all closed incl. canceled; the copilot should ask.")
add("dev", "clarify", "What was our SLA attainment for rodent baiting in 2025?",
    ref="Two SLA definitions exist (share of resolved vs share of all requests); ask which.")
add("jules", "clarify", "How fast did we close 311 requests in October 2026? Give me the median days to close.",
    ref="Two time-to-close definitions; ask which.")
add("lena", "clarify", "What was SLA attainment by department in 2025?",
    ref="Two SLA definitions; ask which.")
add("maria", "sql", "What was the median days to close for CDOT requests created in 2025, including canceled requests (all closed)?",
    sql=f"SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY {DTC}) FROM wh.service_request WHERE closed_at IS NOT NULL AND {Y25}")
add("dev", "sql", "What share of resolved rodent baiting requests created in 2025 were completed within the City's benchmark (SLA "
    "attainment of resolved requests)?",
    sql=SLA_RES.format(dtc=RDTC, where="r.sr_type = 'Rodent Baiting/Rat Complaint' AND r.created_at >= TIMESTAMP '2025-01-01' "
                                       "AND r.created_at < TIMESTAMP '2026-01-01'"))
add("lena", "sql", "What was SLA attainment of all requests within benchmark (including open and canceled as misses) for Streets "
    "and Sanitation requests created in 2025?",
    sql=SLA_ALL.format(dtc=RDTC, where="r.dept_code = 'DSS' AND r.created_at >= TIMESTAMP '2025-01-01' AND r.created_at < "
                                       "TIMESTAMP '2026-01-01'"))
add("priya", "sql", "What was the median days to close for Water Management requests created in 2025, completed requests only?",
    sql=f"SELECT {MED_C} FROM wh.service_request WHERE status = 'Completed' AND closed_at IS NOT NULL AND {Y25}")

assert len(Q) == 60, len(Q)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("".join(json.dumps(q) + "\n" for q in Q))
print(f"wrote {len(Q)} -> {OUT}")
