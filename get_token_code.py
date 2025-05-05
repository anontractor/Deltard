
import time

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from logger import logger


def get_token_code():
    if torch.cuda.is_available():
        torch.cuda.set_device(0)

    trained = YOLO(r'./model/rsa_token_reader_model.pt', task='detect')

    # Initialize the webcam
    video_capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    time.sleep(1)  # Give the webcam time to initialize

    # Check if the webcam is opened successfully
    if not video_capture.isOpened():
        raise IOError("Cannot open webcam")

    # Capture a frame from the webcam
    ret, img_original = video_capture.read()
    video_capture.release()

    if not ret or img_original is None:
        raise RuntimeError("Failed to capture image from webcam.")

    frame = np.array(img_original)

    # Convert to grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Apply Contrast Limited Adaptive Histogram Equalization (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Optional: Resize to match training size
    resized = cv2.resize(enhanced, (416, 416))  # Match training resolution

    inference_img = cv2.cvtColor(resized, cv2.COLOR_GRAY2RGB)

    results = trained(inference_img)
    read_code = ''.join(
        [x['name'] for x in sorted(results[0].summary(), key=lambda x: x['box']['x1'])]
    )
    logger.info('Read code from token: %s', read_code)
    if len(read_code) != 6:
        raise ValueError('Invalid code length')

    return read_code


# def get_token_code():
#     return input('Enter code:')
