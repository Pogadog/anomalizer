import enum

class Health(str, enum.Enum):
    UP = 'up'
    DOWN = 'down'
    UNKNOWN = 'unknown'
    HEALTHY = 'healthy'
    UNHEALTHY = 'unhealthy'

