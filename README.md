# Forecast assistant

The forecast assistant is a customizable application template for building AI-powered forecasts. In addition to creating a hosted and shareable user interface, the forecast assistant provides: 

* Best-in-class predictive model training and deployment using DataRobot forecasting.
* An intelligent explanation of factors driving the forecast that are uniquely derived for any series at any time.

> [!WARNING]
> Application templates are intended to be starting points that provide guidance on how to develop, serve, and maintain AI applications.
> They require a developer or data scientist to adapt and modify them to meet business requirements before being put into production.

![Using forecastic](https://s3.amazonaws.com/datarobot_public/drx/recipe_gifs/launch_gifs/forecast-assistant-smallest.gif)

## Table of contents
1. [Quick Start](#-quick-start)
2. [Architecture overview](#architecture-overview)
3. [Why build AI Apps with DataRobot app templates?](#why-build-ai-apps-with-datarobot-app-templates)
4. [Make changes](#make-changes)
   - [Change the data and how the model is trained](#change-the-data-and-how-the-model-is-trained)
   - [Disable the LLM](#disable-the-llm)
   - [Change the LLM](#change-the-llm)
   - [Change the front-end](#change-the-front-end)
   - [Change the language in the front-end](#change-the-language-in-the-front-end)
5. [Share results](#share-results)
6. [Delete all resources](#delete-all-provisioned-resources)
7. [Setup for advanced users](#setup-for-advanced-users)
8. [Data privacy](#data-privacy)

## 🚀 Quick Start

### Quickstart with DataRobot CLI

#### 1. Install the DataRobot CLI

If you haven't already, install the DataRobot CLI by following the installation instructions at:  
https://github.com/datarobot-oss/cli?tab=readme-ov-file#installation

#### 2. Start the Application

Run the following command to start the local development environment. An interactive wizard will guide you through the selection of configuration options, including creating a `.env` file in the root directory and populating it with environment variables you specify during the wizard.

```sh
dr start
```

The DataRobot CLI (`dr`) will:
- Guide you through configuration setup
- Create and populate your `.env` file with the necessary environment variables
- Deploy your application to DataRobot
- Display a link to your running application when complete

When deployment completes, the terminal will display a link to your running application.  
👉 **Click the link to open and start using your app!**

### Build in Codespace

If you're using **DataRobot Codespace**, everything you need is already installed.
Follow the steps below to launch the entire application in just a few minutes.

Use the built-in terminal on the left sidebar of the Codespace.

From the project root:

```sh
dr start
```

When deployment completes, the terminal will display a link to your running application.\
👉 **Click the link to open and start using your app!**

### Template Development

For local development, follow all of the steps below.

#### 1. Install Pulumi (if you don't have it yet)

If Pulumi is not already installed, follow the installation instructions in the Pulumi [documentation](https://www.pulumi.com/docs/iac/download-install/).
After installing for the first time, **restart your terminal** and run:

```sh
pulumi login --local      # omit --local to use Pulumi Cloud (requires an account)
```

#### 2. Clone the Template Repository

```bash
git clone https://github.com/datarobot-community/forecast-assistant.git
cd forecast-assistant
```

#### 3. Create and Populate Your `.env` File
Run the following command to launch an interactive wizard that helps you create and populate your `.env` file based on `.env.template` and walks you through the required credentials setup.
```sh
dr dotenv setup
```
If you want to locate the credentials manually:

- DataRobot API Token:
  See Create a DataRobot API Key in the [DataRobot API Quickstart docs](https://docs.datarobot.com/en/docs/api/api-quickstart/index.html#create-a-datarobot-api-key).

- DataRobot Endpoint:
  See Retrieve the API Endpoint in the same [Quickstart docs](https://docs.datarobot.com/en/docs/api/api-quickstart/index.html#retrieve-the-api-endpoint).

- LLM Endpoint & API Key (Azure OpenAI):
  This template is pre-configured to use an Azure OpenAI endpoint. If you wish to use a different provider, see [Change the LLM](#change-the-llm).

#### 4. Run the Application

In a terminal, run the following command:

```bash
python quickstart.py YOUR_PROJECT_NAME  # Windows users may have to use `py` instead of `python`
```
Python 3.9+ is required.

Advanced users who want to control virtual environment creation, dependency installation, environment variable setup,
and `pulumi` invocation, see [the advanced setup instructions](#setup-for-advanced-users).


## Architecture overview
![Forecast assistant](https://s3.us-east-1.amazonaws.com/datarobot_public/drx/recipe_gifs/forecast-assistant-diagram.svg)

App Templates contain three families of complementary logic. For this template, you can [opt-in](#make-changes) to fully 
custom AI logic and a fully custom front-end or utilize DataRobot's off-the-shelf offerings:

- **AI logic**: Necessary to service AI requests, generate predictions, and manage predictive models.
  ```
  notebooks/  # Model training logic, scoring data prep logic
  ```
- **App logic**: Necessary for user consumption, whether via a hosted front-end or integrating into an external consumption layer.
  ```
  frontend/  # Streamlit frontend
  forecastic/  # App biz logic & runtime helpers
  ```
- **Operational logic**: Necessary to turn on all DataRobot assets.
  ```
  infra/  # Settings for resources and assets to be created in DataRobot
  infra/__main__.py  # Pulumi program for configuring DataRobot to serve and monitor AI and App logic
  ```


## Why build AI Apps with DataRobot app templates?

App templates transform your AI projects from notebooks to production-ready applications. Too often, getting models into production means rewriting code, juggling credentials, and coordinating with multiple tools and teams just to make simple changes. DataRobot's composable AI apps framework eliminates these bottlenecks, letting you spend more time experimenting with your ML and app logic and less time wrestling with plumbing and deployment.

- Start building in minutes: Deploy complete AI applications instantly, then customize AI logic or front-end independently - no architectural rewrites needed.
- Keep working your way: Data scientists keep working in notebooks, developers in IDEs, and configs stay isolated - update any piece without breaking others.
- Iterate with confidence: Make changes locally and deploy with confidence - spend less time writing and troubleshooting plumbing, more time improving your app.

Each template provides an end-to-end AI architecture, from raw inputs to deployed application, while remaining highly customizable for specific business requirements.

## Make changes

### Change the data and how the model is trained
1. Edit the following two notebooks:
   - `notebooks/train_model.ipynb`: Handles training data ingest and preparation and model training settings.
   - `notebooks/prep_scoring_data.ipynb`: Handles scoring data preparation (the data used to show forecasts in the front-end).
   
   The last cell of each notebook is required, as it writes outputs needed for the rest of the pipeline.

**Recent improvements in `train_model.ipynb`:**
- **Dual-mode operation**: The notebook now supports both training new models and using existing deployments
- **Automatic metadata extraction**: When using an existing deployment, the notebook automatically extracts model metadata (target, datetime partition column, etc.)
- **Flexible feature configuration**: Easy configuration of known-in-advance features for what-if analysis
- **Error handling**: Improved error handling with fallback mechanisms for missing model metadata

2. Run the revised notebooks.
3. Run `pulumi up` to update your stack with these changes.
```bash
source set_env.sh  # On windows use `set_env.bat`
pulumi up
```  
4. For a forecasting app that is continuously updated, consider running `prep_scoring_data.ipynb` on a schedule.

### Disable the LLM
In `infra/settings_generative.py`: Set `LLM=None` to disable any generative output altogether.

### Use an existing forecast deployment

To deploy the app against an existing DataRobot time series deployment (no model training):

**1. Configure `.env`**

```
FORECAST_DEPLOYMENT_ID=<your-deployment-id>
FORECAST_SCORING_DATASET_ID=<your-scoring-dataset-id>   # AI Catalog dataset ID
```

Set `LLM=None` in `infra/settings_generative.py` if you do not need the AI narrative feature.

**2. Update `feature_settings_config` in `notebooks/train_model.ipynb`**

Find the `feature_settings_config` list in the SKIP_TRAINING branch and replace it with the known-in-advance features for your model:
```python
feature_settings_config=[
    FeatureSettingConfig(feature_name="Your_Feature_Name", known_in_advance=True),
    # add all known-in-advance features from your deployment
]
```

**3. Run the training notebook to generate app settings**

The notebook skips model training and extracts metadata (target, datetime column, feature impact) from the existing deployment. It must run before `pulumi up`.

```bash
source set_env.sh         # loads .env + activates .venv
pulumi stack init <name>  # or: pulumi stack select <name>
cd notebooks
papermill train_model.ipynb /dev/null
cd ..
```

This writes `forecastic/train_model_output.<stack-name>.yaml` — the config file the app reads at startup.

**4. Deploy**

```bash
pulumi up
```

> **⚠️ Note:** When using an existing deployment, `pulumi up` skips batch prediction job and retraining policy creation. The app is wired to the deployment and scoring dataset you specified — no new DR resources are trained.

### Change the LLM

1. Modify the `LLM` setting in `infra/settings_generative.py` by changing `LLM=LLMs.AZURE_OPENAI_GPT_4_O_MINI` to any other LLM from the `LLMs` object. 
     - Trial users: Please set `LLM=LLMs.AZURE_OPENAI_GPT_4_O_MINI` since GPT-4o is not supported in the trial. Use the `OPENAI_API_DEPLOYMENT_ID` in `.env` to override which model is used in your azure organisation. You'll still see GPT 4o-mini in the playground, but the deployed app will use the provided azure deployment.  
2. To use an existing TextGen model or deployment:
      - In `infra/settings_generative.py`: Set `LLM=LLMs.DEPLOYED_LLM`.
      - In `.env`: Set either the `TEXTGEN_REGISTERED_MODEL_ID` or the `TEXTGEN_DEPLOYMENT_ID`
      - In `.env`: Set `CHAT_MODEL_NAME` to the model name expected by the deployment (e.g. "claude-3-7-sonnet-20250219" for an anthropic deployment, "datarobot-deployed-llm" for NIM models ) 
3. In `.env`: If not using an existing TextGen model or deployment, provide the required credentials dependent on your choice.
4. Run `pulumi up` to update your stack (Or rerun your quickstart).
      ```bash
      source set_env.sh  # On windows use `set_env.bat`
      pulumi up
      ```

> **⚠️ Availability information:**  
> Using a NIM model requires custom model GPU inference, a premium feature. You will experience errors by using this type of model without the feature enabled. Contact your DataRobot representative or administrator for information on enabling this feature.

### Change the front-end
1. Ensure you have already run `pulumi up` at least once (to provision the time series deployment).
2. Streamlit assets are in `frontend/` and can be edited. After provisioning the stack
   at least once, you can also test the front-end locally using `streamlit run app.py` from the
   `frontend/` directory (don't forget to initialize your environment using `source set_env.sh`).
```bash
source set_env.sh  # On windows use `set_env.bat`
cd frontend
streamlit run app.py
```
3. Run `pulumi up` again to update your stack with the changes.
```bash
source set_env.sh  # On windows use `set_env.bat`
pulumi up
```

#### Change the language in the front-end
Optionally, you can set the application locale in `forecastic/i18n.py`, e.g. `APP_LOCALE = LanguageCode.JA`. Supported locales are Japanese and English, with English set as the default.

#### Application resources
The application now supports inheriting resource configurations from the Application Source. When the Application Source is created, the system automatically fetches its resource settings (replicas, memory, CPU) via the DataRobot API and applies them to the Custom Application.

**How it works:**
1. The Application Source is created with its resource configuration
2. The system fetches the source's resource details using `application_source.id`
3. These resources are automatically applied to the Custom Application

**Environment variables required:**
- `DATAROBOT_ENDPOINT`: Your DataRobot API endpoint
- `DATAROBOT_API_TOKEN`: Your DataRobot API token

**Fallback behavior:**
- If resources cannot be fetched from the Application Source, the system falls back to DataRobot's automatic resource allocation
- Error messages are logged as warnings, ensuring deployment continues successfully

### Environment Variables

The following environment variables can be configured in your `.env` file:

**Required for all deployments:**
- `DATAROBOT_ENDPOINT`: Your DataRobot API endpoint (e.g., `https://app.datarobot.com`)
- `DATAROBOT_API_TOKEN`: Your DataRobot API token

**Optional for existing deployments:**
- `FORECAST_DEPLOYMENT_ID`: ID of an existing forecast deployment to reuse instead of creating a new one
- `TEXTGEN_REGISTERED_MODEL_ID`: ID of an existing registered model for LLM functionality
- `TEXTGEN_DEPLOYMENT_ID`: ID of an existing LLM deployment for LLM functionality
- `CHAT_MODEL_NAME`: Model name for LLM deployments (e.g., "claude-3-7-sonnet-20250219", "datarobot-deployed-llm")

**Optional for LLM providers:**
- `OPENAI_API_KEY`: OpenAI API key (for OpenAI LLMs)
- `OPENAI_API_DEPLOYMENT_ID`: Azure OpenAI deployment ID (for Azure OpenAI)
- `ANTHROPIC_API_KEY`: Anthropic API key (for Claude models)
- `GOOGLE_API_KEY`: Google API key (for Google LLMs)

**Optional for advanced configuration:**
- `DATAROBOT_DEFAULT_USE_CASE`: Use case ID to associate with the project

## Share results
1. Log into the DataRobot application.
2. Navigate to **Registry > Applications**.
3. Navigate to the application you want to share, open the actions menu, and select **Share** from the dropdown.

## Delete all provisioned resources
```bash
pulumi down
```
Then run the jupyter notebook `notebooks/delete_non_pulumi_assets.ipynb`.

## Setup for advanced users
For manual control over the setup process, adapt the following steps for MacOS/Linux to your environent:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
source set_env.sh
pulumi stack init YOUR_PROJECT_NAME
pulumi up 
```
e.g., for Windows/conda/cmd.exe the previous example would change to the following:
```bash
conda create --prefix .venv pip
conda activate .\.venv
pip install -r requirements.txt
set_env.bat
pulumi stack init YOUR_PROJECT_NAME
pulumi up 
```
For projects that will be maintained, DataRobot recommends forking the repo so upstream fixes and improvements can be merged in the future.

## Data privacy
Your data privacy is important to DataRobot. Data handling is governed by the DataRobot [Privacy Policy](https://www.datarobot.com/privacy/). Review the policy before using your own data with DataRobot.
