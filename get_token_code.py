"""Read the current RSA SecurID code using the vendored rsa_reader pipeline.

The rsa_reader package (copied from the rsa-reader project) runs a 3-stage
YOLO pipeline with temporal voting: it only returns a code once 8 of the last
10 decoded frames agree, so failures surface as timeouts rather than wrong
codes. Returns the bare 6-digit code; PIN prefix/postfix are applied by the
caller from secrets.env.

The reader instance is cached so retries within one run don't reload models.
"""

from logger import logger
from rsa_reader import RSATokenReader, load_config

_reader = None


def get_token_code() -> str:
    global _reader
    if _reader is None:
        _reader = RSATokenReader(load_config())

    code = _reader.read()  # raises TimeoutError if no stable code emerges
    logger.info("Read code from token: %s", code)
    return code
