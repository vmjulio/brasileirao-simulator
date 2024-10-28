# brasileirao-simulator

First time running: `make all` and magic happens

To persist the results and sum them up, just change the following lines of code in the file `/src/brasileirao_simulator/entrypoints`:

```python
    params = SimulationParams(iterations=100, load_results=True)
```
This will make the application persist the results in a pickle file, and sum the results for every run of the container.
