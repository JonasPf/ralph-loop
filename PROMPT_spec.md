Help me write or update a specification for a software project. If there are already files in ./specs, study them first. Then interview me in detail using the AskUserQuestionTool about anything. Be very in-depth and continue interviewing me until you have all the information needed. Then create a new branch and name it after the features or project I want to build. Then create or update the specifications in ./specs/.

Rules for writing the specification:

0. Keep the specification as concise and succinct as possible. Avoid bloat.
1. Do not refer to the current state of the project, instead write or update the specs so they are self-contained and can be read and understood without prior knowledge of the project.
2. Keep the what (Functionality, Use Cases, Acceptance Criteria) seperate from the how (Tech Stack, Architecture). IMPORTANT: A specification is not a plan, do not include specific impmlementation steps.
3. Use OpenAPI 3.0 to design APIs.
4. Use mermaid entity relation diagram to document data bases and other data models.
5. Use mermaid flowcharts or message sequence diagrams to document data flows and system interaction.
6. Every feature must be documented with acceptance criteria.
7. Include a requirement for a good test suite consisting of unit tests, integration and e2e tests.
8. Include a requirment for concise and succinct documentation and a getting started guide in README.md.
9. Include a requirement for linting the code base.

Once you've written the specification, study it again and point out any inconsistencies, gaps or blindspots. If there are any lets resolve them together.
