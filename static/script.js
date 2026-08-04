document.getElementById('careerForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    const role = document.getElementById('role').value;
    const github = document.getElementById('github').value;
    const resumeFile = document.getElementById('resume').files[0];

    if (!resumeFile) {
        alert('Please upload your resume PDF');
        return;
    }

    const resumeText = await resumeFile.text();

    const query = `
Analyze my resume for the role of ${role}.

Resume:
${resumeText}

GitHub username: ${github}

Give me:
1. ATS score
2. Skill gaps
3. GitHub evaluation
4. Recommended projects
5. Job opportunities
6. Placement readiness score
`;

    try {
        const response = await fetch('/career-agent/invoke', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                input: query
            })
        });

        const data = await response.json();

        const result = data.output || JSON.stringify(data);

        document.getElementById('atsScore').textContent = '--';
        document.getElementById('readinessScore').textContent = '--';
        document.getElementById('githubScore').textContent = '--';

        document.getElementById('githubEval').innerHTML =
            '<li>Analysis completed</li><li>Check backend response</li>';

        document.getElementById('projects').innerHTML =
            '<li>AI Resume Analyzer</li><li>Placement Dashboard</li>';

        document.getElementById('jobs').innerHTML =
            '<div class="jobs-item"><h4>Analysis generated successfully</h4></div>';

        alert(result);

    } catch (err) {
        console.error(err);
        alert('Failed to analyze profile');
    }
});
