CRISP EMAIL AGENT - SUBJECT DROPDOWN FIX

Files:
- crisp_email_agent.py: updated automation script with exact Crisp custom-dropdown matching.
- gui_app.py: your existing GUI, unchanged. It already launches crisp_email_agent.py from the same folder.

Installation:
1. Back up your current project folder.
2. Replace crisp_email_agent.py with the file in this package.
3. Keep gui_app.py and your existing CSV/config files in the same project folder.
4. Start the GUI as usual.
5. Keep Dry Run enabled for the first test.

What changed:
- Targets Crisp's observed .c-base-select-menu-item__label and its parent
  .c-base-select-menu-item__wrapper.
- Requires an exact subject text match and verifies that the dropdown row
  closes after the click.
- Does not press Enter as a generic fallback, because Enter may submit the
  Create Conversation form.
- Stops safely if the suggestion selection cannot be confirmed.

Validation performed:
- Python syntax compilation passed for both Python files.
- Live Crisp interaction was not run in this environment. Test with Dry Run
  enabled before turning real sending on.
