document.getElementById('careerForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    const role = document.getElementById('role').value;
    const github = document.getElementById('github').value.trim();
    const resume = document.getElementById('resume').files[0];
    const formError = document.getElementById('formError');
    formError.hidden = true;

    if (!resume) {
        formError.textContent = 'Please upload your resume PDF.';
        formError.hidden = false;
        return;
    }
    if (!github) {
        formError.textContent = 'Please enter a GitHub username.';
        formError.hidden = false;
        return;
    }

    const formData = new FormData();
    formData.append('role', role);
    formData.append('github', github);
    formData.append('resume', resume);

    const button = document.querySelector('.primary-btn');
    button.disabled = true;
    button.textContent = 'Analyzing...';

    try {
        const response = await fetch('/analyze', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Analysis failed. Please try again.');
        }

        renderResults(data);
    } catch (err) {
        console.error(err);
        formError.textContent = err.message || 'Analysis failed. Please try again.';
        formError.hidden = false;
        document.getElementById('resultsSection').hidden = true;
        document.getElementById('emptyState').hidden = false;
    } finally {
        button.disabled = false;
        button.textContent = 'Analyze my profile';
    }
});

function renderResults(data) {
    document.getElementById('emptyState').hidden = true;
    document.getElementById('resultsSection').hidden = false;

    document.getElementById('atsScore').textContent = data.ats_score;
    document.getElementById('atsNote').textContent = data.ats_feedback || '';

    document.getElementById('readinessScore').textContent = data.placement_readiness;

    document.getElementById('githubScore').textContent = data.github_score;
    const langs = (data.github_top_languages && data.github_top_languages.length)
        ? ` - ${data.github_top_languages.join(', ')}`
        : '';
    document.getElementById('githubNote').textContent =
        `${data.github_public_repos || 0} public repos - ${data.github_followers || 0} followers${langs}`;

    const skillGap = document.getElementById('skillGap');
    skillGap.innerHTML = (data.missing_skills && data.missing_skills.length)
        ? data.missing_skills.map(skill => `<span class="chip danger">${escapeHtml(skill)}</span>`).join('')
        : '<p class="muted">No major skill gaps detected.</p>';

    const githubEval = document.getElementById('githubEval');
    githubEval.innerHTML = (data.github_recommendations && data.github_recommendations.length)
        ? data.github_recommendations.map(item => `<li>${escapeHtml(item)}</li>`).join('')
        : '<li>No recommendations available.</li>';

    const projects = document.getElementById('projects');
    projects.innerHTML = (data.projects && data.projects.length)
        ? data.projects.map(project => `<li>${escapeHtml(project)}</li>`).join('')
        : '<li>No project suggestions available.</li>';

    const jobs = document.getElementById('jobs');
    jobs.innerHTML = (data.jobs && data.jobs.length)
        ? data.jobs.map(job => {
            const meta = [job.company, job.location].filter(Boolean).join(' - ');
            return `
            <div class="jobs-item">
                <h4>${escapeHtml(job.title)}</h4>
                ${meta ? `<p>${escapeHtml(meta)}</p>` : ''}
                ${job.url ? `<a href="${job.url}" target="_blank" rel="noopener">Apply</a>` : ''}
            </div>`;
          }).join('')
        : '<p class="muted">No jobs found right now.</p>';

    const roadmap = document.getElementById('roadmapTimeline');
    roadmap.innerHTML = (data.roadmap && data.roadmap.length)
        ? data.roadmap.map(step => `
            <div class="step">
                <span>${escapeHtml(step.week)}${step.focus ? ' - ' + escapeHtml(step.focus) : ''}</span>
                <p>${escapeHtml(step.tasks)}</p>
            </div>`).join('')
        : '<p class="muted">No roadmap generated.</p>';
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
}
