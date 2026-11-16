def is_policy_rejection(text):
    text = text.lower()
    return any(value in text for value in (
        'misalignment_policy_violation', 'content_policy_violation',
        'blocked by our safety systems',
    ))
