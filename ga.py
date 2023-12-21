from openai import OpenAI
from sklearn.metrics.pairwise import cosine_similarity
import logging
import subprocess
import numpy as np

from utils.utils import *


class G2A:
    def __init__(self, cfg, root_dir) -> None:
        self.client = OpenAI()
        self.cfg = cfg
        self.root_dir = root_dir
        
        self.iteration = 0
        self.function_evals = 0
        self.elitist = None
        self.best_obj_overall = float("inf")
        
        self.init_prompt()

        self.init_population()
        
        self.ga_crossover_prompt = file_to_string(f'{root_dir}/utils/prompts_ga/crossover.txt')
        self.ga_mutate_prompt = file_to_string(f'{root_dir}/utils/prompts_ga/mutate.txt')
        
        self.print_cross_prompt = True # Print crossover prompt for the first iteration
        self.print_mutate_prompt = True # Print mutate prompt for the first iteration


    def init_prompt(self) -> None:
        self.problem = self.cfg.problem.problem_name
        self.problem_description = self.cfg.problem.description
        self.problem_size = self.cfg.problem.problem_size
        
        logging.info("Problem: " + self.problem)
        logging.info("Problem description: " + self.problem_description)
        
        prompt_dir = f'{self.root_dir}/utils/prompts_{self.cfg.problem.problem_type}'
        problem_dir = f"{self.root_dir}/problems/{self.problem}"
        self.output_file = f"{self.root_dir}/problems/{self.problem}/{self.cfg.suffix.lower()}.py"
        
        # Loading all text prompts
        self.seed_function = file_to_string(f'{problem_dir}/seed.txt')
        self.generator_system_prompt = file_to_string(f'{self.root_dir}/utils/prompts_general/system_generator.txt')
        self.initial_user = file_to_string(f'{prompt_dir}/initial_user.txt').format(problem_description=self.problem_description)

        
    def init_population(self) -> None:
        # Generate responses
        messages = [
            {"role": "system", "content": self.generator_system_prompt},
            {"role": "user", "content": self.initial_user},
            {"role": "assistant", "content": self.seed_function},
            {"role": "user", "content": "Improve over the above code. \n[code]:\n"}
        ]
        logging.info(
            "Initial Population Prompt: \nSystem Prompt: \n" + self.generator_system_prompt +
            "\nUser Prompt: \n" + self.initial_user +
            "\nAssistant Prompt: \n" + self.seed_function
        )
        responses = chat_completion(self.cfg.pop_size, messages, self.cfg.model, self.cfg.temperature)
        
        # Responses to population
        population = self.responses_to_population(responses)
        
        # Run code and evaluate population
        population = self.evaluate_population(population)
        objs = [individual["obj"] for individual in population]
        
        # Bookkeeping
        self.best_obj_overall, best_sample_idx = min(objs), np.argmin(np.array(objs))
        self.best_code_overall = population[best_sample_idx]["code"]
        self.best_code_path_overall = population[best_sample_idx]["code_path"]

        # Update iteration
        self.population = population
        self.update_iter()

    
    def evaluate_greedy_alg(self) -> float:
        """
        Generate and evaluate the greedy algorithm for the problem, e.g. Nearest Neighbor for TSP.
        """
        # Loading all text prompts
        greedy_alg_tip = file_to_string(f'{self.root_dir}/utils/prompts_general/gen_greedy_tip.txt')
        messages = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": self.initial_user + greedy_alg_tip}]
        logging.info("Greedy Algorithm Prompt: \nSystem Prompt: \n" + self.system_prompt + "\nUser Prompt: \n" + self.initial_user + greedy_alg_tip)
        
        # Generate responses
        responses = chat_completion(1, messages, self.cfg.model, self.cfg.temperature)
        
        # Response to individual
        individual = self.response_to_individual(responses[0], 0, file_name="greedy_alg")
        
        # Run code and evaluate population
        population = self.evaluate_population([individual])
        return population[0]["obj"]

    
    def response_to_individual(self, response, response_id, file_name=None) -> dict:
        """
        Convert response to individual
        """
        code = process_code(response.message.content)
        # Write response to file
        file_name = f"problem_iter{self.iteration}_response{response_id}.txt" if file_name is None else file_name + ".txt"
        with open(file_name, 'w') as file:
            file.writelines(code + '\n')

        # Extract code and description from response
        std_out_filepath = f"problem_iter{self.iteration}_stdout{response_id}.txt" if file_name is None else file_name + "_stdout.txt"
        
        individual = {
            "stdout_filepath": std_out_filepath,
            "code_path": f"problem_iter{self.iteration}_code{response_id}.py",
            "code": code,
            "response_id": response_id,
        }

        return individual

        
    def responses_to_population(self, responses) -> list[dict]:
        """
        Convert responses to population. Applied to the initial population.
        """
        population = []
        for response_id, response in enumerate(responses):
            individual = self.response_to_individual(response, response_id)
            population.append(individual)
        return population

    @staticmethod
    def mark_invalid_individual(individual: dict, traceback_msg: str) -> dict:
        """
        Mark an individual as invalid.
        """
        individual["exec_success"] = False
        individual["obj"] = float("inf")
        individual["fitness"] = 0
        individual["traceback_msg"] = traceback_msg
        return individual

    def evaluate_population(self, population: list[dict]) -> list[float]:
        """
        Evaluate population by running code in parallel and computing objective values and fitness.
        """
        inner_runs = []
        # Run code to evaluate
        for response_id in range(len(population)):
            
            # Skip if response is invalid
            if population[response_id]["code"] is None:
                population[response_id] = self.mark_invalid_individual(population[response_id], "Invalid response!")
                inner_runs.append(None)
                continue
            
            logging.info(f"Iteration {self.iteration}: Running Code {response_id}")
            self.function_evals += 1
            
            try:
                process = self.run_code(population[response_id], response_id)
                inner_runs.append(process)
            except Exception as e: # If code execution fails
                logging.info(f"Error for response_id {response_id}: {e}")
                population[response_id] = self.mark_invalid_individual(population[response_id], str(e))
                inner_runs.append(None)
        
        # Update population with objective values and fitness
        for response_id, inner_run in enumerate(inner_runs):
            if inner_run is None: # If code execution fails, skip
                continue
            try:
                inner_run.communicate(timeout=20) # Wait for code execution to finish
            except subprocess.TimeoutExpired as e:
                logging.info(f"Error for response_id {response_id}: {e}")
                population[response_id] = self.mark_invalid_individual(population[response_id], str(e))
                inner_run.kill()
                continue

            individual = population[response_id]
            stdout_filepath = individual["stdout_filepath"]
            with open(stdout_filepath, 'r') as f:  # read the stdout file
                stdout_str = f.read() 
            traceback_msg = filter_traceback(stdout_str)
            
            individual = population[response_id]
            # Store objective value and fitness for each individual
            if traceback_msg == '': # If execution has no error
                try:
                    individual["obj"] = float(stdout_str.split('\n')[-2])
                    assert individual["obj"] > 0, "Objective value <= 0 is not supported."
                    individual["fitness"] = 1 / individual["obj"]
                    individual["exec_success"] = True
                except:
                    population[response_id] = self.mark_invalid_individual(population[response_id], "Invalid std out / objective value!")
            else: # Otherwise, also provide execution traceback error feedback
                population[response_id] = self.mark_invalid_individual(population[response_id], traceback_msg)

            logging.info(f"Iteration {self.iteration}, response_id {response_id}: Objective value: {individual['obj']}")
        return population


    def run_code(self, individual: dict, response_id) -> subprocess.Popen:
        """
        Write code into a file and run eval script.
        """
        logging.debug(f"Iteration {self.iteration}: Processing Code Run {response_id}")
        
        with open(self.output_file, 'w') as file:
            file.writelines(individual["code"] + '\n')

        # Execute the python file with flags
        with open(individual["stdout_filepath"], 'w') as f:
            process = subprocess.Popen(['python', '-u', f'{self.root_dir}/problems/{self.problem}/eval.py', f'{self.problem_size}', self.root_dir],
                                        stdout=f, stderr=f)

        block_until_running(individual["stdout_filepath"], log_status=True, iter_num=self.iteration, response_id=response_id)
        return process

    
    def update_iter(self) -> None:
        """
        Update after each iteration
        """
        population = self.population
        objs = [individual["obj"] for individual in population]
        best_obj, best_sample_idx = min(objs), np.argmin(np.array(objs))
        
        # update best overall
        if best_obj < self.best_obj_overall:
            self.best_obj_overall = best_obj
            self.best_code_overall = population[best_sample_idx]["code"]
            self.best_desc_overall = population[best_sample_idx]["description"]
            self.best_code_path_overall = population[best_sample_idx]["code_path"]
        
        # update elitist
        if self.elitist is None or best_obj < self.elitist["obj"]:
            self.elitist = population[best_sample_idx]
            logging.info(f"Iteration {self.iteration}: Elitist: {self.elitist['obj']}")
        
        logging.info(f"Iteration {self.iteration} finished...")
        logging.info(f"Min obj: {self.best_obj_overall}, Best Code Path: {self.best_code_path_overall}")
        logging.info(f"Function Evals: {self.function_evals}")
        self.iteration += 1
    
    
    def random_select(self, population: list[dict]) -> list[dict]:
        """
        Random selection, select individuals with equal probability. Used for comparison.
        """
        selected_population = []
        # Eliminate invalid individuals
        population = [individual for individual in population if individual["exec_success"]]
        for _ in range(self.cfg.pop_size):
            parents = np.random.choice(population, size=2, replace=False)
            selected_population.extend(parents)
        assert len(selected_population) == 2*self.cfg.pop_size
        return selected_population


    def crossover(self, population: list[dict]) -> list[dict]:
        crossed_population = []
        assert len(population) == self.cfg.pop_size * 2
        
        messages_lst = []
        for i in range(0, len(population), 2):
            # Select two individuals
            parent_1 = population[i]
            parent_2 = population[i+1]
            
            # Crossover
            crossover_prompt_user = self.ga_crossover_prompt.format(
                problem_description=self.problem_description,
                code1=parent_1["code"],
                code2=parent_2["code"],
                description1=parent_1["description"],
                description2=parent_2["description"],
                )
            messages = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": crossover_prompt_user }]
            messages_lst.append(messages)
            
            # Print crossover prompt for the first iteration
            if self.print_cross_prompt:
                logging.info("Crossover Prompt: \nSystem Prompt: \n" + self.system_prompt + "\nUser Prompt: \n" + crossover_prompt_user )
                self.print_cross_prompt = False
        
        # Multi-processed chat completion
        responses_lst = multi_chat_completion(messages_lst, 1, self.cfg.model, self.cfg.temperature)
        response_id = 0
        for i in range(len(responses_lst)):
            individual = self.response_to_individual(responses_lst[i][0], response_id)
            crossed_population.append(individual)
            response_id += 1

        assert len(crossed_population) == self.cfg.pop_size
        return crossed_population


    def mutate(self, population: list[dict]) -> list[dict]:
        messages_lst = []
        response_id_lst = []
        for i in range(len(population)):
            individual = population[i]
            
            # Mutate
            if np.random.uniform() < self.cfg.mutation_rate:
                mutate_prompt = self.ga_mutate_prompt.format(
                    problem_description=self.problem_description,
                    code=individual["code"],
                    description=individual["description"]
                    )
                messages = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": mutate_prompt}]
                messages_lst.append(messages)
                response_id_lst.append(i)
                # Print mutate prompt for the first iteration
                if self.print_mutate_prompt:
                    logging.info("Mutate Prompt: \nSystem Prompt: \n" + self.system_prompt + "\nUser Prompt: \n" + mutate_prompt)
                    self.print_mutate_prompt = False
            
        # Multi-processed chat completion
        responses_lst = multi_chat_completion(messages_lst, 1, self.cfg.model, self.cfg.temperature)
        for i in range(len(responses_lst)):
            response_id = response_id_lst[i]
            mutated_individual = self.response_to_individual(responses_lst[i][0], response_id)
            population[response_id] = mutated_individual

        assert len(population) == self.cfg.pop_size
        return population


    def evolve(self):
        while self.function_evals < self.cfg.max_fe:
            # Diversify
            if self.cfg.diversify: self.diversify()
            # Select
            population_to_select = self.population if self.elitist is None else [self.elitist] + self.population # add elitist to population for selection
            selected_population = self.random_select(population_to_select)
            # Crossover
            crossed_population = self.crossover(selected_population)
            # Mutate
            mutated_population = self.mutate(crossed_population)
            # Evaluate
            population = self.evaluate_population(mutated_population)
            # Update
            self.population = population
            self.update_iter()
        return self.best_code_overall, self.best_desc_overall, self.best_code_path_overall


    @staticmethod
    def compute_similarity(descriptions: list[str], client: OpenAI, model: str="text-embedding-ada-002") -> np.ndarray:
        """
        Embed code descriptions using OpenAI's embedding API and compute the cosine similarity matrix.
        """
        response = client.embeddings.create(
            input=descriptions,
            model=model,
        )
        embeddings = [_data.embedding for _data in response.data]
        similarity_matrix = cosine_similarity(embeddings)
        return similarity_matrix


    @staticmethod
    def adjust_similarity_matrix(similarity_matrix: np.ndarray, population: list[dict]) -> np.ndarray:
        """
        For those with identical objective values, set their similarity to 1.
        """
        for i in range(len(population)):
            for j in range(i+1, len(population)):
                if population[i]["obj"] == population[j]["obj"]:
                    similarity_matrix[i][j] = 1
                    similarity_matrix[j][i] = 1
        return similarity_matrix
