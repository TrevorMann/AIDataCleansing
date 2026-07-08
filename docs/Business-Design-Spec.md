# Business Design Spec - 2026-06-01

This project is a data initialization agent for organizations, the purpose of this project is to help organizations align and annotate their data, allow for creating some standard cleaning methods and alignment.  As much autonomous aspects to this workflow is encouraged.  This project acts as the following. CLients will come with general data models of actual current data, this project will create look up tables to help facilitate and clean and provide data annotations based on context from the domain and industry that it researches as well as the data in the columns.  it will use smart sampling to reduce costs, and then follow up and ask any questions where ambiguity exists.  The final output is a schema with cleaning and look up tables that can help facilitate data processing,  annotations of every column in the schema / table the user selects and a collaboration through questions and answer to ensure business context is captured and used.


## General Flow:

Connect to database --> Define data_details schema for the agent to add its data annotation suggestions and cleaning / look up tables for that domain --> identify the schema or schema(s) that the user would like data to be cleaned and annotated for --> Go out and research appropriate data cleaning based on data knowledge industry best practices and skills that are defined in the agent project to enhance data and how to research and clean appropriately --> give suggestions and ask questions and have iterative loops to help the user give business context.  This context must be stored for continuous improvement and used. 

Ensures all steps of the agent process are document and streamed to the output window so user can track where the agent application is and what it is doing,  do not wait till the end. 


Agent Order:

Researcher --> Business manager --> Data engineer --> Schema manager 

can talk to others when agent feels necessary but standard order.



## Security

The Agent must not read the .env or any credentials it must only call functions or code to get connection and details.  
The Agent needs DDL access on the data_details schema and read only on the schema(s) with client data

## Researcher

Researches the domain that is being initialzed, goes through its own knowledge and inference (based on model) and gathers details in the data space based on the subject area questions asked during initialization.  
It must keep to the specific domain and not go outside the domain, if details from Data engineering and annotation / cleaning are needed outside you must make sure it relates back to the specific domain
You must only make suggestions which will enhance the data pipeline, if unclear ask follow up questions
When a new domain is initialized we have seed tables which help with look up and general details which you are researching to populate
You are responsible for the research and suggestions and to pass off to the next agent / skill.   
You are allowed to suggest any changes to schema / structure or columns and data types or procoes however you MUST be explicit when you know there will be a breaking change and pass to the next agent
The researcher outputs a .md file with recommendations and examples based on its research to hand off to the Data Engineer and Data Schema Manager.
Researcher ensures the artifacts are saved and viewable for audit purposes and includes clear reasoning on decisions.

## Business Manager
The business manager works with the Researcher to vet that what the researcher returns is domain specific.
The business manger becomes an expert in the domain field (example sports_ticketing on how people buy ticket ticketing data and what fields / information are required for analytics and analysis). 
The Business manger takes the output from the researcher (md file) and verifies it based on its expertise in the agents domain.
If the business manager cannot verify researcher results and align with domain then it sends it back to Researcher for further clarification. 
It will only try 3 times per question to send back to researcher else it will get user confirmation with the question.

## Data Schema Manager

Purpose of the data schema manager is to create and perform the data schema aspects
It must write code and excute SQL statements on the desired architecture (back-end) to facilitate the recommendations of the researcher 
it will generate any config files needed to track and manage the schema, data types and annotation of the columns.
This is a critical key component as data annotation for later AI use is crucial so the annotations must adhere to standards such as:
   - Explaining data in the column based on the domain, be very specific
   - Able to be read from LLMs and AI agents to give details and provide better queries and functionality and efficiencies
   - Include data types it knows about and data structures to help give context
   - Relate it to other tables and data when sure to enhance the context of the column description
   - Iterate over multiple tables and structures finding what it needs to give better context to the annotation of the data column being explicit and providing examples where required.
Does the following operations:
    - CTAS when a new table is created from other data
    - Has or creates functions to load data into current tables
    - Creates new tables based on Data engineer recommendations on the data_details schema
    - Communicates additional tables that client could created based on Data Engineer suggestions on their main schemas
    - Aligns with the specifications 


## Data Engineer (Fact-checker)
The data engineers agent job is to check the results of the researcher after a business manager review. and that it aligns with general data engineering practices and technologies,  it will suggest the simplest method that will develop a poc and get the client going. 
The data engineer is an expert in the data field, it knows to research for best practices and structures based on the technologies used by the user.
The data engineer will be responsible for asking what back end technologies are used and setting up all necessary files and structure to connect and get credentials 
all credentials will live in the .env file for this project 
it will check the results of the data schema and code manager for 
    - syntax
    - appropriateness of structure
    - data types 
    - flow and alignment across the project.
The data engineer takes the researcher MD and validates it against the current project and code and scope as well as current database alignment
The Data engineer flags any issues, conflicts or breaking of data practices such as: 
    - Data Type alignment for column description
    - Data connection works and is secure
    - Foreign and primary keys exist and are unique where appropriate
    - Flags issues where tables are too wide or not performant.
The Data engineer refines the researchers spec into actionable data cleaning / SQL data annotation processes and code
The Data engineer hands off to the Data Schema manager to perform the schema updates. 

## Shared Domain Isolation

- utilize tagging of rows / data enhancement and cleaning in a domain specific manner
- Ensure when initializing to not touch other domains data if cleaning data or templates are there go through a new domain initalization. 
- Keep domains separate even if they have same or similar data. 

## AI Models used
- Validate you have a working model
- Allow switching of model based on users configuration and needs
- If model cannot perform a task do research to let the user know what model may be better to use for the specific output.

## Requirements

- Connect to databases, for now postgres and sqlite
- Each instance or install of this package / project / agent will be able to initialize 1 to many domains for an organization so must handle multi domain configs and connections
- Ensure it does not delete any of the client data, updating clearing out and reseeding the domain is allowed
- Do extensive research on the domain, ask for supporting links if available but otherwise use what you know, do not hallucinate ask if ambiuous
- Think like a human is is learning the domain and trying to clean and annotate the data
- keep the sql queries and data normalized as you can.
- Ensure domain specifics are kept for the domain.
- Does not cross domain specific tables or logic
- The agent will take checkpoints throughout the process to allow for resumption of data annotation and cleaning. this is to ensure we don't need to re read or do everything again if there is an error or pausing.
- Agent will resume where last left off, (take a snapshot) so when further runs are done it will pick up changes and add additional details this is a living continuous improvement agent.
- A domain specific memory will be created and used to gather and iterate over details (learning more about the domain) every run,  the agent process will update and keep track of this memory for each domain so all personas can read it 
- No Business or domain specific logic is in the personas they are just used to generate, use deterministic functions and processes and perform their appropriate tasks. utilize domain specific memory that all personas can access for domain knowledge learning.

## Error handling / escalations
- If an error occurs that blocks further process alert the user and document the reason why,  give suggestions how to fix or address the issue, do not leave the user hanging without any feedback.
- If an escalation is needed then stop and prompt the user for details. 
- If error is unrecoverable ensure error is logged, display then exit.
- If schema execution fails track what steps were performed and ensure to give user a rollback script. 


## Acceptance Criteria
- User has a table that contains records for all columns in the tables configured with annotated descriptions that align with the domain and tables structures.
- Any researched data points and data cleaning look ups are present and populated 
- A document exists which outlines all decisions and research made and why annotations were done and what data was populated for look up / cleaning and spell checking. 
- Recommendations on structure improvements or additional tables which were not able to be created or updated. do not leave the user hanging 

### Notes:

TODO FUTURE: 
 - Data lineage
 - Reading and initalizing based on dbt or other frameworks to help get more details.
