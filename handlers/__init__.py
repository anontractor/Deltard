from .fsprov_login import (
    is_username_prompt_visible, handle_username_prompt,
    is_password_prompt_visible, handle_password_prompt,
    is_token_prompt_visible, handle_token_prompt
)
from .microsoft_prompt import (
    is_stay_signed_in_prompt, handle_stay_signed_in_prompt,
    is_trust_prompt_visible, handle_trust_prompt
)
from.deltek import (
    is_timesheet_page, handle_timesheet_page
)
from .costpoint import (
    is_session_warning_visible, handle_session_warning,
    is_username_input_visible, handle_username_input,
    is_database_prompt_visible, handle_database_prompt,
)

PAGE_DETECTORS = [
    (is_username_prompt_visible, handle_username_prompt),
    (is_password_prompt_visible, handle_password_prompt),
    (is_token_prompt_visible, handle_token_prompt),
    (is_stay_signed_in_prompt, handle_stay_signed_in_prompt),
    (is_session_warning_visible, handle_session_warning),
    (is_username_input_visible, handle_username_input),
    (is_database_prompt_visible, handle_database_prompt),
    (is_trust_prompt_visible, handle_trust_prompt),
    (is_timesheet_page, handle_timesheet_page),

]


async def get_handler_for_page(page):
    for detector, handler in PAGE_DETECTORS:
        if await detector(page):
            return handler

    return None
