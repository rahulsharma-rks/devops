#!/usr/bin/env python3
"""List all IAM users with their policies, categorized by admin-level access."""

import argparse
import boto3
from datetime import datetime, timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference

ADMIN_POLICIES = {
    "arn:aws:iam::aws:policy/AdministratorAccess",
    "arn:aws:iam::aws:policy/IAMFullAccess",
    "arn:aws:iam::aws:policy/PowerUserAccess",
}

POWER_POLICIES = {
    "arn:aws:iam::aws:policy/PowerUserAccess",
    "arn:aws:iam::aws:policy/job-function/NetworkAdministrator",
    "arn:aws:iam::aws:policy/job-function/DatabaseAdministrator",
}

COLORS = {
    "red_dark":    "C00000",
    "red_light":   "FFCCCC",
    "orange_dark": "E36C09",
    "orange_light":"FCE4D6",
    "blue_dark":   "2F5496",
    "blue_light":  "D9E1F2",
    "green_dark":  "375623",
    "green_light": "E2EFDA",
    "grey_dark":   "595959",
    "grey_light":  "F2F2F2",
    "white":       "FFFFFF",
    "header_bg":   "1F3864",
}

THIN_BORDER = Border(
    left=Side(style="thin", color="BFBFBF"),
    right=Side(style="thin", color="BFBFBF"),
    top=Side(style="thin", color="BFBFBF"),
    bottom=Side(style="thin", color="BFBFBF"),
)


def get_iam_client(role_arn=None):
    if role_arn:
        sts = boto3.client("sts")
        creds = sts.assume_role(RoleArn=role_arn, RoleSessionName="iam-audit")["Credentials"]
        return boto3.client(
            "iam",
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    return boto3.client("iam")


iam = None


def is_admin_inline(policy_doc):
    for stmt in policy_doc.get("Statement", []):
        actions = stmt.get("Action", [])
        resources = stmt.get("Resource", [])
        if isinstance(actions, str):
            actions = [actions]
        if isinstance(resources, str):
            resources = [resources]
        if stmt.get("Effect") == "Allow" and "*" in actions and "*" in resources:
            return True
    return False


def has_iam_star(policy_doc):
    """Check if policy grants iam:* (privilege escalation risk)."""
    for stmt in policy_doc.get("Statement", []):
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        if stmt.get("Effect") == "Allow" and any(
            a in ("iam:*", "*") for a in actions
        ):
            return True
    return False


def days_since(dt):
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days


def get_user_detail(username):
    """Return enriched user detail: policies, admin flag, category, MFA, last activity."""
    policies = []
    is_admin = False
    is_power = False
    has_iam_escalation = False

    # Attached managed policies
    for p in iam.list_attached_user_policies(UserName=username)["AttachedPolicies"]:
        arn = p["PolicyArn"]
        policies.append(f"[Direct] {p['PolicyName']}")
        if arn in ADMIN_POLICIES - POWER_POLICIES:
            is_admin = True
        if arn in POWER_POLICIES:
            is_power = True

    # Inline policies
    for name in iam.list_user_policies(UserName=username)["PolicyNames"]:
        doc = iam.get_user_policy(UserName=username, PolicyName=name)["PolicyDocument"]
        admin_flag = is_admin_inline(doc)
        iam_flag = has_iam_star(doc)
        tag = " ⚠ ADMIN-LEVEL" if admin_flag else (" ⚠ IAM-ESCALATION" if iam_flag else "")
        policies.append(f"[Inline] {name}{tag}")
        if admin_flag:
            is_admin = True
        if iam_flag:
            has_iam_escalation = True

    # Group policies
    for g in iam.list_groups_for_user(UserName=username)["Groups"]:
        gname = g["GroupName"]
        for p in iam.list_attached_group_policies(GroupName=gname)["AttachedPolicies"]:
            arn = p["PolicyArn"]
            policies.append(f"[Group:{gname}] {p['PolicyName']}")
            if arn in ADMIN_POLICIES - POWER_POLICIES:
                is_admin = True
            if arn in POWER_POLICIES:
                is_power = True
        for name in iam.list_group_policies(GroupName=gname)["PolicyNames"]:
            doc = iam.get_group_policy(GroupName=gname, PolicyName=name)["PolicyDocument"]
            admin_flag = is_admin_inline(doc)
            iam_flag = has_iam_star(doc)
            tag = " ⚠ ADMIN-LEVEL" if admin_flag else (" ⚠ IAM-ESCALATION" if iam_flag else "")
            policies.append(f"[Group:{gname}/Inline] {name}{tag}")
            if admin_flag:
                is_admin = True
            if iam_flag:
                has_iam_escalation = True

    # Determine category
    if is_admin:
        category = "Admin"
    elif has_iam_escalation:
        category = "Privileged"
    elif is_power:
        category = "Power User"
    elif not policies:
        category = "No Policies"
    else:
        category = "Standard"

    # MFA status
    try:
        mfa_devices = iam.list_mfa_devices(UserName=username)["MFADevices"]
        mfa_enabled = "Yes" if mfa_devices else "No"
    except Exception:
        mfa_enabled = "Unknown"

    # Last activity via access keys
    last_used = None
    try:
        keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
        for key in keys:
            lu = iam.get_access_key_last_used(AccessKeyId=key["AccessKeyId"])
            used_date = lu.get("AccessKeyLastUsed", {}).get("LastUsedDate")
            if used_date:
                if last_used is None or used_date > last_used:
                    last_used = used_date
    except Exception:
        pass

    return {
        "category": category,
        "policies": policies,
        "mfa_enabled": mfa_enabled,
        "last_used": last_used,
        "days_inactive": days_since(last_used),
    }


# ── Styling helpers ────────────────────────────────────────────────────────────

def hdr_cell(ws, row, col, value, bg=COLORS["header_bg"], fg=COLORS["white"], size=11):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=True, color=fg, size=size, name="Arial")
    c.fill = PatternFill("solid", fgColor=bg)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = THIN_BORDER
    return c


def data_cell(ws, row, col, value, bg=COLORS["white"], bold=False, wrap=False, align="left"):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(name="Arial", size=10, bold=bold)
    c.fill = PatternFill("solid", fgColor=bg)
    c.alignment = Alignment(horizontal=align, vertical="top", wrap_text=wrap)
    c.border = THIN_BORDER
    return c


CATEGORY_COLORS = {
    "Admin":      (COLORS["red_dark"],    COLORS["red_light"]),
    "Privileged": (COLORS["orange_dark"], COLORS["orange_light"]),
    "Power User": (COLORS["orange_dark"], COLORS["orange_light"]),
    "Standard":   (COLORS["blue_dark"],   COLORS["blue_light"]),
    "No Policies":(COLORS["grey_dark"],   COLORS["grey_light"]),
}


def write_user_sheet(ws, users, title):
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A3"

    # Title row
    ws.merge_cells("A1:H1")
    c = ws["A1"]
    c.value = title
    c.font = Font(bold=True, color=COLORS["white"], size=13, name="Arial")
    c.fill = PatternFill("solid", fgColor=COLORS["header_bg"])
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    headers = ["#", "User Name", "Category", "Created Date",
               "MFA Enabled", "Days Inactive", "Policies", "Notes"]
    for col, h in enumerate(headers, 1):
        hdr_cell(ws, 2, col, h)
    ws.row_dimensions[2].height = 22

    for idx, u in enumerate(users, 1):
        row = idx + 2
        cat = u["category"]
        _, row_bg = CATEGORY_COLORS.get(cat, (COLORS["blue_dark"], COLORS["blue_light"]))
        alt_bg = "F7FBFF" if idx % 2 == 0 else row_bg

        policies_text = "\n".join(u["policies"]) if u["policies"] else "(no policies attached)"
        days = u["days_inactive"]
        inactive_str = f"{days} days" if days is not None else "Never used"

        # Flag notes
        notes = []
        if cat == "Admin":
            notes.append("⚠ Admin-level access – review immediately")
        if u["mfa_enabled"] == "No":
            notes.append("⚠ MFA not enabled")
        if days is not None and days > 90:
            notes.append(f"⚠ Inactive {days}+ days")
        if cat == "No Policies":
            notes.append("ℹ No policies – consider removing user")

        data_cell(ws, row, 1, idx, alt_bg, align="center")
        data_cell(ws, row, 2, u["name"], alt_bg, bold=(cat == "Admin"))
        # Category badge
        cat_fg, cat_bg = CATEGORY_COLORS.get(cat, (COLORS["blue_dark"], COLORS["blue_light"]))
        c = ws.cell(row=row, column=3, value=cat)
        c.font = Font(name="Arial", size=10, bold=True, color=cat_fg)
        c.fill = PatternFill("solid", fgColor=cat_bg)
        c.alignment = Alignment(horizontal="center", vertical="top")
        c.border = THIN_BORDER

        data_cell(ws, row, 4, u["created"], alt_bg, align="center")
        mfa_bg = COLORS["green_light"] if u["mfa_enabled"] == "Yes" else COLORS["red_light"]
        c = ws.cell(row=row, column=5, value=u["mfa_enabled"])
        c.font = Font(name="Arial", size=10, bold=True,
                      color=COLORS["green_dark"] if u["mfa_enabled"] == "Yes" else COLORS["red_dark"])
        c.fill = PatternFill("solid", fgColor=mfa_bg)
        c.alignment = Alignment(horizontal="center", vertical="top")
        c.border = THIN_BORDER

        inactive_bg = COLORS["red_light"] if (days is not None and days > 90) else alt_bg
        data_cell(ws, row, 6, inactive_str, inactive_bg, align="center")
        data_cell(ws, row, 7, policies_text, alt_bg, wrap=True)
        data_cell(ws, row, 8, "\n".join(notes) if notes else "—", alt_bg, wrap=True)

        line_count = max(policies_text.count("\n") + 1, len(notes) or 1)
        ws.row_dimensions[row].height = max(20, min(line_count * 15, 120))

    col_widths = [5, 24, 14, 14, 12, 14, 58, 38]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # Auto-filter on header row
    ws.auto_filter.ref = f"A2:H{len(users) + 2}"


def write_summary_sheet(ws, all_users):
    ws.sheet_view.showGridLines = False

    # Title
    ws.merge_cells("A1:F1")
    c = ws["A1"]
    c.value = f"IAM User Audit – Summary Dashboard  |  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"
    c.font = Font(bold=True, color=COLORS["white"], size=13, name="Arial")
    c.fill = PatternFill("solid", fgColor=COLORS["header_bg"])
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    # Category counts
    from collections import Counter
    cat_counts = Counter(u["category"] for u in all_users)
    mfa_off = sum(1 for u in all_users if u["mfa_enabled"] == "No")
    inactive_90 = sum(1 for u in all_users if u["days_inactive"] is not None and u["days_inactive"] > 90)

    # KPI boxes (row 3-6)
    kpis = [
        ("Total Users",    len(all_users),                COLORS["blue_dark"],   COLORS["blue_light"]),
        ("Admin Users",    cat_counts.get("Admin", 0),    COLORS["red_dark"],    COLORS["red_light"]),
        ("MFA Disabled",   mfa_off,                       COLORS["orange_dark"], COLORS["orange_light"]),
        ("Inactive 90d+",  inactive_90,                   COLORS["orange_dark"], COLORS["orange_light"]),
        ("Privileged",     cat_counts.get("Privileged", 0) + cat_counts.get("Power User", 0),
                                                           COLORS["orange_dark"], COLORS["orange_light"]),
        ("No Policies",    cat_counts.get("No Policies", 0), COLORS["grey_dark"], COLORS["grey_light"]),
    ]

    for i, (label, val, fg, bg) in enumerate(kpis, 1):
        col = i
        ws.merge_cells(start_row=3, start_column=col, end_row=3, end_column=col)
        ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col)
        lc = ws.cell(row=3, column=col, value=label)
        lc.font = Font(bold=True, name="Arial", size=10, color=fg)
        lc.fill = PatternFill("solid", fgColor=bg)
        lc.alignment = Alignment(horizontal="center", vertical="center")
        lc.border = THIN_BORDER
        vc = ws.cell(row=4, column=col, value=val)
        vc.font = Font(bold=True, name="Arial", size=20, color=fg)
        vc.fill = PatternFill("solid", fgColor=bg)
        vc.alignment = Alignment(horizontal="center", vertical="center")
        vc.border = THIN_BORDER
        ws.row_dimensions[3].height = 20
        ws.row_dimensions[4].height = 36

    for col in range(1, 7):
        ws.column_dimensions[get_column_letter(col)].width = 18

    # Category breakdown table
    ws.merge_cells("A6:F6")
    hdr = ws["A6"]
    hdr.value = "User Breakdown by Category"
    hdr.font = Font(bold=True, color=COLORS["white"], size=11, name="Arial")
    hdr.fill = PatternFill("solid", fgColor=COLORS["header_bg"])
    hdr.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[6].height = 22

    table_headers = ["Category", "Count", "% of Total", "Risk Level", "Recommended Action"]
    for col, h in enumerate(table_headers, 1):
        hdr_cell(ws, 7, col, h)

    risk_map = {
        "Admin":      ("🔴 Critical", "Review & limit immediately; enforce MFA"),
        "Privileged": ("🟠 High",     "Audit permissions; apply least-privilege"),
        "Power User": ("🟠 Medium",   "Validate necessity; monitor activity"),
        "Standard":   ("🟢 Low",      "Routine review; check MFA"),
        "No Policies":("⚪ Info",     "Remove or assign appropriate policies"),
    }
    categories = ["Admin", "Privileged", "Power User", "Standard", "No Policies"]
    total = len(all_users) or 1

    for ridx, cat in enumerate(categories, 8):
        cnt = cat_counts.get(cat, 0)
        pct = f"=B{ridx}/B{ridx - ridx + 8 + len(categories) + 1}"  # formula approach
        risk, action = risk_map[cat]
        bg = CATEGORY_COLORS.get(cat, (COLORS["blue_dark"], COLORS["blue_light"]))[1]
        row_bg = bg if ridx % 2 == 0 else COLORS["white"]

        data_cell(ws, ridx, 1, cat, row_bg, bold=True)
        data_cell(ws, ridx, 2, cnt, row_bg, align="center")
        pct_cell = ws.cell(row=ridx, column=3, value=cnt / total)
        pct_cell.number_format = "0.0%"
        pct_cell.font = Font(name="Arial", size=10)
        pct_cell.fill = PatternFill("solid", fgColor=row_bg)
        pct_cell.alignment = Alignment(horizontal="center", vertical="top")
        pct_cell.border = THIN_BORDER
        data_cell(ws, ridx, 4, risk, row_bg)
        data_cell(ws, ridx, 5, action, row_bg, wrap=True)
        ws.row_dimensions[ridx].height = 22

    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 46

    # Top-10 admin/privileged users quick reference
    risky = [u for u in all_users if u["category"] in ("Admin", "Privileged", "Power User")]
    if risky:
        start_row = 8 + len(categories) + 2
        ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=6)
        h = ws.cell(row=start_row, column=1, value="⚠  High-Risk Users – Quick Reference")
        h.font = Font(bold=True, color=COLORS["white"], size=11, name="Arial")
        h.fill = PatternFill("solid", fgColor=COLORS["red_dark"])
        h.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[start_row].height = 22

        sub_headers = ["User Name", "Category", "MFA", "Days Inactive", "Policy Count", "Notes"]
        for col, sh in enumerate(sub_headers, 1):
            hdr_cell(ws, start_row + 1, col, sh, bg=COLORS["red_dark"])

        for ridx, u in enumerate(risky[:20], start_row + 2):
            notes = []
            if u["mfa_enabled"] == "No":
                notes.append("No MFA")
            if u["days_inactive"] is not None and u["days_inactive"] > 90:
                notes.append(f"Inactive {u['days_inactive']}d")
            days_str = f"{u['days_inactive']}d" if u["days_inactive"] is not None else "Never"
            bg = COLORS["red_light"] if ridx % 2 == 0 else COLORS["white"]
            data_cell(ws, ridx, 1, u["name"], bg, bold=True)
            data_cell(ws, ridx, 2, u["category"], bg)
            data_cell(ws, ridx, 3, u["mfa_enabled"], bg, align="center")
            data_cell(ws, ridx, 4, days_str, bg, align="center")
            data_cell(ws, ridx, 5, len(u["policies"]), bg, align="center")
            data_cell(ws, ridx, 6, "; ".join(notes) if notes else "—", bg)


def main():
    global iam
    parser = argparse.ArgumentParser(description="IAM User Audit Report")
    parser.add_argument("--role-arn", help="IAM role ARN to assume")
    parser.add_argument("-o", "--output", default="iam_audit_report.xlsx", help="Output file")
    args = parser.parse_args()
    iam = get_iam_client(args.role_arn)

    print("Fetching IAM users...")
    raw_users = iam.list_users()["Users"]
    all_users = []

    for i, user in enumerate(raw_users, 1):
        name = user["UserName"]
        print(f"  [{i}/{len(raw_users)}] {name}")
        detail = get_user_detail(name)
        all_users.append({
            "name": name,
            "created": user["CreateDate"].strftime("%Y-%m-%d"),
            **detail,
        })

    # Sort: admin first, then privileged, then rest
    order = {"Admin": 0, "Privileged": 1, "Power User": 2, "Standard": 3, "No Policies": 4}
    all_users.sort(key=lambda u: order.get(u["category"], 9))

    wb = Workbook()

    # Sheet 1: Summary
    ws_summary = wb.active
    ws_summary.title = "📊 Summary"
    write_summary_sheet(ws_summary, all_users)

    # Sheet 2: All Users
    ws_all = wb.create_sheet("👥 All Users")
    write_user_sheet(ws_all, all_users, "All IAM Users – Complete Audit")

    # Sheet 3: Admin users only
    admin_users = [u for u in all_users if u["category"] == "Admin"]
    ws_admin = wb.create_sheet("🔴 Admin Users")
    write_user_sheet(ws_admin, admin_users, "Admin-Level Users – Requires Immediate Review")

    # Sheet 4: Privileged/Power users
    priv_users = [u for u in all_users if u["category"] in ("Privileged", "Power User")]
    ws_priv = wb.create_sheet("🟠 Privileged Users")
    write_user_sheet(ws_priv, priv_users, "Privileged & Power Users")

    # Sheet 5: Standard users
    std_users = [u for u in all_users if u["category"] == "Standard"]
    ws_std = wb.create_sheet("🟢 Standard Users")
    write_user_sheet(ws_std, std_users, "Standard Users")

    wb.save(args.output)
    print(f"\n✅ Report saved: {args.output}")
    print(f"   Admin:      {len(admin_users)}")
    print(f"   Privileged: {len(priv_users)}")
    print(f"   Standard:   {len(std_users)}")
    print(f"   Total:      {len(all_users)}")


if __name__ == "__main__":
    main()