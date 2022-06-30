import enum

class Health(str, enum.Enum):
    UP = 'up'
    DOWN = 'down'
    HEALTHY = 'healthy'
    UNHEALTHY = 'unhealthy'

