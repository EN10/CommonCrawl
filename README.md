# Common Crawl Search

A Flask web application that allows users to search and retrieve archived web pages from Common Crawl's web archive using their CDX API.

## Features

- Search for archived versions of any URL in Common Crawl's database
- Binary search algorithm for efficient index searching
- Real-time search progress updates
- Display of technical metadata for archived pages
- Download capability for original archived files
- Asset retrieval from Common Crawl archives (CSS, JS, images)
- Responsive web interface

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/EN10/CommonCrawl.git
   cd CommonCrawl
   ```

2. Create a virtual environment and activate it:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Start the Flask application:
   ```bash
   python app.py
   ```

2. Open your web browser and navigate to:
   ```
   http://localhost:5000
   ```

3. Enter a URL in the search box and click "Search" to find archived versions.

## Technical Details

- Uses Flask for the web framework
- Implements binary search across Common Crawl indexes for efficient searching
- Handles various URL formats and variations
- Supports gzip compression for WARC file handling
- Includes comprehensive error handling and logging
- Features server-sent events for real-time search progress updates

## Project Structure

```
CommonCrawl/
├── app.py              # Main application file with full features
├── app_simple.py       # Simplified version of the application
├── requirements.txt    # Python dependencies
├── LICENSE            # MIT License
├── README.md          # This file
└── templates/
    └── index.html     # Web interface template
```

## Dependencies

- Flask==3.0.0
- requests==2.31.0
- warcio==1.7.4

## How It Works

1. **URL Search**: When a user submits a URL, the application first normalizes it to ensure consistent searching.

2. **Index Search**: The app performs a binary search across Common Crawl indexes to find the most recent capture of the domain.

3. **Content Retrieval**: Once found, the application:
   - Retrieves the archived content from Common Crawl's WARC files
   - Processes and fixes relative URLs in the content
   - Handles asset retrieval (images, CSS, JS) from the archive

4. **Progress Updates**: Real-time updates are sent to the client using Server-Sent Events (SSE).

## API Endpoints

- `GET /`: Main search interface
- `POST /`: Handle search submissions
- `/search-progress`: SSE endpoint for real-time search updates
- `/asset`: Endpoint for retrieving archived assets
- `/download-file`: Endpoint for downloading original archived files

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## Best Practices

- Use the simplified version (`app_simple.py`) for basic usage
- Use the full version (`app.py`) for advanced features
- Keep rate limits in mind when making requests to Common Crawl
- Consider implementing caching for frequently accessed content

## Troubleshooting

Common issues and solutions:

1. **Rate Limiting**: If you encounter rate limiting, add delays between requests
2. **Timeout Errors**: Adjust the timeout parameters in the requests
3. **Memory Issues**: Consider implementing pagination for large results

## Acknowledgments

- [Common Crawl](https://commoncrawl.org/) for providing the web archive data
- The Flask team for the excellent web framework
- All contributors who participate in this project

## Support

For support, please:
1. Check the troubleshooting section
2. Search existing issues
3. Open a new issue with:
   - Detailed description of the problem
   - Steps to reproduce
   - Error messages and logs

## Future Improvements

- [ ] Add result caching
- [ ] Implement pagination for search results
- [ ] Add support for advanced search filters
- [ ] Improve asset handling performance
- [ ] Add API documentation using Swagger/OpenAPI