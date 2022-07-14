import json, os
from confluent_kafka import Consumer, KafkaError

class KafkaConsumer:
    def __init__(self, *args, **kwargs):
        self.topic = kwargs['topic']
        config = {
            'bootstrap.servers': kwargs['bootstrap_servers'],
            'security.protocol': 'SASL_SSL',
            'sasl.mechanisms': 'PLAIN',
            'sasl.username': kwargs['sasl_username'],
            'sasl.password': kwargs['sasl_password'],
            'session.timeout.ms': '45000',
            'group.id': 'python-consumer-group-1',
            'auto.offset.reset': 'latest'
        }

        self.consumer = Consumer(config)
        # Subscribe to topic
        self.consumer.subscribe([self.topic])

    def process(self):
        # Process messages
        total_count = 0
        try:
            while True:
                msg = self.consumer.poll(1.0)
                if msg is None:
                    # No message available within timeout.
                    # Initial message consumption may take up to
                    # `session.timeout.ms` for the consumer group to
                    # rebalance and start consuming
                    #print("Waiting for message or event/error in poll()")
                    continue
                elif msg.error():
                    print('error: {}'.format(msg.error()))
                else:
                    # Check for Kafka message
                    record_key = msg.key().decode()
                    record_value = json.loads(msg.value().decode())
                    count = 1
                    total_count += count
                    print(record_key + ':' + str(record_value))
        except KeyboardInterrupt:
            pass
        finally:
            # Leave group and commit final offsets
            self.consumer.close()

kafka = KafkaConsumer(bootstrap_servers=os.environ.get('BOOTSTRAP_SERVERS'),
          topic='loki-anomalizer',
          sasl_username=os.environ.get('SASL_USERNAME'),
          sasl_password=os.environ.get('SASL_PASSWORD')).process()