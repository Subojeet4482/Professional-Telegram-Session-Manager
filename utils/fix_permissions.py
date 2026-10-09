import os
import glob
import re

# Resolve handlers/ relative to the project root, regardless of where this
# script is invoked from. The script now lives in utils/, one level deeper.
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
handlers_dir = os.path.join(BASE_DIR, "handlers")

read_only_funcs = {
    "sessions.py": [
        "cb_sessions_menu",
        "cb_session_list",
        "cb_search",
        "msg_search",
        "cb_stats",
        "cb_country_sessions",
        "cb_phone_detail"
    ],
    "settings.py": [
        "cb_settings_menu",
        "cb_proxy_settings",
        "cb_api_settings",
        "cb_profile_settings"
    ],
    "stats_chart_handler.py": [
        "cb_stats_chart",
        "msg_stats_chart"
    ],
    "start.py": [
        "cmd_start",
        "cb_menu",
        "cb_cancel",
        "cb_noop"
    ],
    "admin_handler.py": [
        "cb_action_logs"
    ]
}

def process_file(filepath):
    filename = os.path.basename(filepath)
    if filename in ["common.py", "__init__.py"]:
        return

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    if "is_admin_async" in content and "is_write_admin_async" not in content:
        content = re.sub(
            r"from handlers\.common import (.*?is_admin_async.*)",
            r"from handlers.common import \1, is_write_admin_async",
            content
        )

    lines = content.split('\n')
    current_func = None
    allowed_funcs = read_only_funcs.get(filename, [])
    
    for i, line in enumerate(lines):
        m = re.match(r'^async def\s+([a-zA-Z0-9_]+)\s*\(', line)
        if not m:
            m = re.match(r'^def\s+([a-zA-Z0-9_]+)\s*\(', line)
        if m:
            current_func = m.group(1)
            
        if current_func not in allowed_funcs:
            if "is_admin_async(" in line:
                lines[i] = line.replace("is_admin_async(", "is_write_admin_async(")
                
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

for pyfile in glob.glob(os.path.join(handlers_dir, "*.py")):
    process_file(pyfile)

print("Done replacing permissions.")
