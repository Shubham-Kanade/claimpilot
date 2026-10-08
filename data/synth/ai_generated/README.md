# AI-generated receipt images

This folder holds receipt images made with image-generation models. They test whether the trust
checks flag synthetic-looking documents. They are **added by hand**: the generator does not create
them, and no API is called to make them.

When you add one:
- Use fictional merchants only, never real people, and no prompts that copy a real bill.
- Put a `ReceiptTruth` JSON next to the image with the same file stem. Include the tag `ai_generated` and describe only what is printed.
- Keep each file small (under about 500 KB).
