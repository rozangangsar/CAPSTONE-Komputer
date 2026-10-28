"""Current capacitive hardware contract; packet order is never anatomically reordered."""
SAMPLING_RATE = 30
INPUT_LENGTH = 60
STRIDE = 15
SENSOR_LAYOUT = [
    dict(index=0, channel='0x2A CH0', location='heel'),
    dict(index=1, channel='0x2A CH1', location='midfoot centre'),
    dict(index=2, channel='0x2A CH2', location='forefoot lateral tip'),
    dict(index=3, channel='0x2A CH3', location='midfoot near heel'),
    dict(index=4, channel='0x2B CH0', location='forefoot medial base'),
    dict(index=5, channel='0x2B CH1', location='forefoot medial tip'),
    dict(index=6, channel='0x2B CH2', location='forefoot lateral base'),
    dict(index=7, channel='0x2B CH3', location='midfoot near forefoot'),
]
