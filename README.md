# Large Language Models as Meta-Optimizers


### Usage

- Set your OpenAI API key as an environment variable `OPENAI_API_KEY`.
- Make sure to generate datasets before running. Refer to `problems/*/dataset/readme.md` for details.
- Running logs and intermediate results are saved in `./outputs/main/` by default.


```bash
# for tsp_constructive
python main.py problem=tsp_constructive

# for tsp_aco
python main.py problem=tsp_aco

# for cvrp_aco
python main.py problem=cvrp_aco

# for mkp_aco
python main.py problem=mkp_aco

# for online BPP
python main.py problem=bpp_online
```


### Dependency

You may install the dependencies via `pip install -r ./requirements.txt`.

- Python >= 3.9
- openai >= 1.0.0
- hydra-core
- scikit-learn
- scipy

*In our implementation, solving problems using ACO requires [pytorch](https://pytorch.org/), which is not included in `requirements.txt`.*

### Acknowledgments
- [DeepACO: Neural-enhanced Ant Systems for Combinatorial Optimization](https://github.com/henry-yeh/DeepACO)
- [Mathematical discoveries from program search with large language models](https://github.com/google-deepmind/funsearch)
- [Eureka: Human-Level Reward Design via Coding Large Language Models](https://github.com/eureka-research/Eureka)