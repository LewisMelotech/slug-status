# A list of constants that should be consistent across database/form usage
MAX_SCRIPT_NAME_LENGTH = 100
MAX_AUTHOR_NAME_LENGTH = 100
STANDARD_TEENSYVILLE_CHARACTER_COUNT = 12
MAX_CHARACTER_COUNT = 25

# Custom script slugs. Deliberately shorter than the script name: a slug is
# meant to be typed by hand (in a URL, or into a Discord command).
MAX_SLUG_LENGTH = 50
MIN_SLUG_LENGTH = 1

# Free text for whoever sets a script up on the Minecraft server, e.g. its colour.
MAX_MINECRAFT_CUSTOMISATIONS_LENGTH = 500

MAX_JSON_UPLOAD_BYTES = 1024 * 1024
MAX_PDF_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_JSON_UPLOAD_BYTES + MAX_PDF_UPLOAD_BYTES + 1024 * 1024
