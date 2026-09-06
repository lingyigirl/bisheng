from .base import BaseErrorCode


# GPU resource management module error codes, business code 111
class GpuNodeExistsError(BaseErrorCode):
    Code: int = 11100
    Msg: str = "GPU node host already exists"


class GpuNodeNotFoundError(BaseErrorCode):
    Code: int = 11101
    Msg: str = "GPU node does not exist"


class GpuNodeConnectError(BaseErrorCode):
    Code: int = 11102
    Msg: str = "Failed to connect to GPU node"


class GpuNodePasswordRequiredError(BaseErrorCode):
    Code: int = 11103
    Msg: str = "SSH password is required"


class InvalidContainerNameError(BaseErrorCode):
    Code: int = 11104
    Msg: str = "Invalid container name"


class InvalidModelPathError(BaseErrorCode):
    Code: int = 11105
    Msg: str = "Invalid model path"


class InvalidGpuIndexError(BaseErrorCode):
    Code: int = 11106
    Msg: str = "Invalid GPU index"


class DeployTemplateNotFoundError(BaseErrorCode):
    Code: int = 11107
    Msg: str = "Deploy template does not exist"


class DeployInstanceNotFoundError(BaseErrorCode):
    Code: int = 11108
    Msg: str = "Deploy instance does not exist"


class DeployRunError(BaseErrorCode):
    Code: int = 11109
    Msg: str = "Failed to run container"


class DeployOperationError(BaseErrorCode):
    Code: int = 11110
    Msg: str = "Container operation failed"


class DeleteModelFileError(BaseErrorCode):
    Code: int = 11111
    Msg: str = "Failed to delete model file"


class InvalidBrowsePathError(BaseErrorCode):
    Code: int = 11112
    Msg: str = "Path is outside the model directory"
