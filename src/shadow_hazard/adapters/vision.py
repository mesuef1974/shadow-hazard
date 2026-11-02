"""Synthetic calibrated overhead camera per source; HSV marker detections only."""
import base64
from shadow_hazard.domain.motion import BUS


class MarkerCamera:
    version = 'hsv-marker-v1'

    def __init__(self):
        import cv2
        import numpy as np
        self.cv, self.np = cv2, np

    def capture(self, actor, position, visible=True, preview=False):
        cv, np = self.cv, self.np
        image = np.full((600, 200, 3), 35, dtype=np.uint8)
        # Explicit synthetic affine calibration: 10 pixels/metre; origin (-5,-38).
        bus_start = (round((BUS[0]+5)*10), round((BUS[1]+38)*10))
        bus_end = (round((BUS[2]+5)*10), round((BUS[3]+38)*10))
        cv.rectangle(image, bus_start, bus_end, (70, 150, 200), -1)
        pixel = (round((position[0]+5)*10), round((position[1]+38)*10))
        if visible:
            cv.circle(image, pixel, 4, (255, 0, 0) if actor == 'car' else (0, 255, 0), -1)
        hsv = cv.cvtColor(image, cv.COLOR_BGR2HSV)
        hue = 120 if actor == 'car' else 60
        mask = cv.inRange(hsv, (hue-5, 150, 100), (hue+5, 255, 255))
        moments = cv.moments(mask)
        found = None
        if moments['m00']:
            found = (moments['m10']/moments['m00']/10-5,
                     moments['m01']/moments['m00']/10-38)
        png = None
        if preview:
            png = base64.b64encode(cv.imencode('.png', image)[1]).decode('ascii')
        return found, png
