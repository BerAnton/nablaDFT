import os
from typing import List

import hydra
from omegaconf import DictConfig
from pytorch_lightning.loggers import Logger
from pytorch_lightning import (
    LightningDataModule,
    LightningModule,
    Trainer,
    Callback,
    seed_everything,
)

from nablaDFT.utils import close_loggers, write_predictions_to_db, set_additional_params
from nablaDFT.optimization import BatchwiseOptimizeTask
from nablaDFT import model_registry


JOB_TYPES = ["train", "test", "predict", "optimize"]


def check_cfg_parameters(cfg: DictConfig):
    job_type = cfg.get("job_type")
    if job_type not in JOB_TYPES:
        raise ValueError(f"job_type must be one of {JOB_TYPES}, got {job_type}")
    if cfg.pretrained and cfg.ckpt_path:
        raise ValueError("Config parameters ckpt_path and pretrained are mutually exclusive. Consider set ckpt_path "
                         "to null, if you plan to use pretrained checkpoints.")


def predict(
    trainer: Trainer,
    model: LightningModule,
    datamodule: LightningDataModule,
    config: DictConfig,
):
    """Function for prediction loop execution.
    Saves model prediction to "predictions" directory.

    Args:
        config (DictConfig): config for task. see r'config/' for examples.
    """
    trainer.logger = False  # need this to disable save_hyperparameters during predict,
    # otherwise OmegaConf DictConf can't be dumped to YAML
    pred_path = os.path.join(os.getcwd(), "predictions")
    os.makedirs(pred_path, exist_ok=True)
    predictions = trainer.predict(
        model=model, datamodule=datamodule
    )
    write_predictions_to_db()


def optimize(config: DictConfig):
    # TODO: refactor me
    """Function for batched molecules optimization.
    Uses model defined in config.

    Args:
        config (DictConfig): config for task. see r'config/' for examples.
    """
    raise NotImplementedError
    output_dir = config.get("output_dir")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    output_datapath = os.path.join(
        output_dir, f"{config.name}_{config.dataset_name}.db"
    )
    # update existing database not supported
    if os.path.exists(output_datapath):
        os.remove(output_datapath)
    calculator = hydra.utils.instantiate(config.calculator, model)
    optimizer = hydra.utils.instantiate(config.optimizer, calculator)
    task = BatchwiseOptimizeTask(
        config.input_db,
        output_datapath,
        optimizer=optimizer,
        batch_size=config.batch_size,
    )
    task.run()


def run(config: DictConfig):
    """Main function to perform task runs on nablaDFT datasets.
    Refer to r'nablaDFT/README.md' for detailed description of run configuration.

    Args:
        config (DictConfig): config for task. see r'config/' for examples.
    """
    check_cfg_parameters(config)
    if config.get("seed"):
        seed_everything(config.seed)
    job_type = config.job_type
    if config.get("ckpt_path"):
        ckpt_path = os.path.join(
            hydra.utils.get_original_cwd(), config.get("ckpt_path")
        )
    else:
        ckpt_path = None
    config = set_additional_params(config)
    # download checkpoint if pretrained=True
    if job_type == "optimize":
        return optimize(config)
    if config.get("pretrained"):
        model: LightningModule = model_registry.get_pretrained_model("lightning", config.pretrained)
    else:
        model: LightningModule = hydra.utils.instantiate(config.model)
    # Callbacks
    callbacks: List[Callback] = []
    for _, callback_cfg in config.callbacks.items():
        callbacks.append(hydra.utils.instantiate(callback_cfg))
    # Loggers
    loggers: List[Logger] = []
    for _, logger_cfg in config.loggers.items():
        loggers.append(hydra.utils.instantiate(logger_cfg))
    # Trainer
    trainer: Trainer = hydra.utils.instantiate(
        config.trainer, callbacks=callbacks, logger=loggers
    )
    # Datamodule
    datamodule: LightningDataModule = hydra.utils.instantiate(config.datamodule)
    if job_type == "train":
        trainer.fit(model=model, datamodule=datamodule, ckpt_path=ckpt_path)
    elif job_type == "test":
        trainer.test(model=model, datamodule=datamodule, ckpt_path=ckpt_path)
    elif job_type == "predict":
        predict(trainer, model, datamodule, config)
    # Finalize
    close_loggers(
        logger=loggers,
    )
