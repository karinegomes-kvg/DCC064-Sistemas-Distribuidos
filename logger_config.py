import logging
import os

def configurar_logger(nome):
    verbose = os.getenv("DNS_VERBOSE", "false").lower() == "true"

    logger = logging.getLogger(nome)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "[%(levelname)s] [%(name)s] %(message)s"
            )
        )
        logger.addHandler(handler)

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    return logger