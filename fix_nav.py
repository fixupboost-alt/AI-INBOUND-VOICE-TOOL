"""Fix nav HTML - replace inline onclick with data-page attributes"""
import re

with open('ui_server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find sidebar-nav block using line numbers approach
lines = content.split('\n')
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if '<div class="sidebar-nav">' in line and start_idx is None:
        start_idx = i
    if start_idx and '</div>' in line and i > start_idx + 10:
        # Check if this closes the sidebar-nav (next line should be sidebar-footer)
        if i + 1 < len(lines) and 'sidebar-footer' in lines[i + 1]:
            end_idx = i
            break

if start_idx is None or end_idx is None:
    print(f"Could not find sidebar-nav block. start={start_idx}, end={end_idx}")
    # Debug: show what's around sidebar-nav
    for i, line in enumerate(lines):
        if 'sidebar' in line.lower():
            print(f"L{i+1}: {repr(line[:100])}")
else:
    print(f"Found sidebar-nav at lines {start_idx+1} to {end_idx+1}")
    new_nav_lines = [
        '  <div class="sidebar-nav">',
        '    <div class="nav-section">Overview</div>',
        '    <div class="nav-item" data-page="dashboard"><span class="icon">&#x1F4CA;</span> Dashboard</div>',
        '    <div class="nav-item" data-page="calendar"><span class="icon">&#x1F4C5;</span> Calendar</div>',
        '    <div class="nav-section" style="margin-top:12px;">Configuration</div>',
        '    <div class="nav-item" data-page="agent"><span class="icon">&#x1F916;</span> Agent Settings</div>',
        '    <div class="nav-item" data-page="models"><span class="icon">&#x1F399;&#xFE0F;</span> Models &amp; Voice</div>',
        '    <div class="nav-item" data-page="credentials"><span class="icon">&#x1F511;</span> API Credentials</div>',
        '    <div class="nav-section" style="margin-top:12px;">Data</div>',
        '    <div class="nav-item" data-page="logs"><span class="icon">&#x1F4DE;</span> Call Logs</div>',
        '    <div class="nav-item" data-page="crm"><span class="icon">&#x1F465;</span> CRM Contacts</div>',
        '    <div class="nav-section" style="margin-top:12px;">Calling</div>',
        '    <div class="nav-item" data-page="outbound"><span class="icon">&#x1F4F2;</span> Outbound Calls</div>',
        '    <div class="nav-item" data-page="languages"><span class="icon">&#x1F310;</span> Language Presets</div>',
        '    <div class="nav-item" data-page="demo"><span class="icon">&#x2728;</span> Demo Link</div>',
        '  </div>',
    ]
    lines[start_idx:end_idx+1] = new_nav_lines
    new_content = '\n'.join(lines)
    with open('ui_server.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"SUCCESS: replaced {end_idx - start_idx + 1} lines with {len(new_nav_lines)} new lines")
