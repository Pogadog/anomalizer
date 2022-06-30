from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest

LIMITS = [0.1, 0.01, 0.001, 0.0001, 0.00001, 0.0000001, 0.00000001, 0.000000001, 0]

C_EXCEPTIONS_HANDLED = Counter('anomalizer_num_exceptions_handled', 'number of exeptions handled', ['exception'])
S_TO_IMAGE = Summary('anomalizer_to_image_time', 'time to convert images')
